# 【已停用】高德地图工具：开发环境测试通过，但部署环境中无法执行，v2.0 已从组织文化助手中移除挂载，代码保留供参考。
# API Key 通过环境变量 AMAP_WEB_API_KEY 读取，仓库中不包含任何密钥。

import os
import json
import logging
from typing import Optional, Union

import requests
from langchain.tools import tool
from pydantic import Field

from tools._circuit_breaker import circuit_break

logger = logging.getLogger(__name__)

AMAP_BASE_URL = "https://restapi.amap.com/v3"
AMAP_TIMEOUT = 8  # seconds（部署环境到高德接口的网络可能不稳定，严格 8 秒超时）


def _get_api_key() -> str:
    key = os.getenv("AMAP_WEB_API_KEY")
    if not key:
        raise RuntimeError("未配置高德地图 API Key（环境变量 AMAP_WEB_API_KEY 未设置）")
    return key


def _normalize_city(v) -> Optional[str]:
    """将 city/字符串/列表等各种输入容错为单个字符串或 None。"""
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        v = v[0] if v else None
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _classify_error(e: Exception) -> str:
    """把 requests 异常分类为可读的错误类型字符串。"""
    import urllib3
    import socket

    if isinstance(e, requests.exceptions.Timeout):
        return "超时（连接或读取超过8秒）"
    if isinstance(e, requests.exceptions.ConnectTimeout):
        return "连接超时（无法在8秒内建立连接）"
    if isinstance(e, requests.exceptions.ReadTimeout):
        return "读取超时（连接已建立但8秒内未收到响应）"
    if isinstance(e, requests.exceptions.SSLError):
        return f"SSL 错误：{e.__class__.__name__}"
    if isinstance(e, requests.exceptions.ConnectionError):
        msg = str(e)
        # DNS 解析失败 / 连接被拒绝 / 其他连接错误
        args = getattr(e, "args", None)
        reason = None
        if args:
            inner = args[0] if args else None
            if isinstance(inner, urllib3.exceptions.MaxRetryError):
                reason_obj = getattr(inner, "reason", None)
                if isinstance(reason_obj, urllib3.exceptions.NewConnectionError):
                    conn_errs = getattr(reason_obj, "args", [])
                    for ce in conn_errs:
                        if isinstance(ce, socket.gaierror):
                            return "DNS 解析失败（无法解析 restapi.amap.com 域名）"
                        if isinstance(ce, ConnectionRefusedError) or isinstance(ce, ConnectionError):
                            msg2 = str(ce)
                            if "refused" in msg2.lower() or "拒绝" in msg2:
                                return "连接被拒绝（restapi.amap.com 拒绝连接，可能是端口不通或IP封禁）"
                    return f"连接失败：{str(inner)[:200]}"
                if isinstance(reason_obj, socket.timeout):
                    return "连接超时（TCP 建连超时）"
                reason = str(reason_obj)[:200] if reason_obj else None
            if isinstance(inner, socket.gaierror):
                return "DNS 解析失败（无法解析 restapi.amap.com 域名）"
        return f"连接错误：{reason or msg[:200]}"
    if isinstance(e, requests.exceptions.HTTPError):
        return f"HTTP 错误：{str(e)[:200]}"
    return f"{e.__class__.__name__}：{str(e)[:200]}"


class AmapNetworkError(RuntimeError):
    """高德网络不可达的语义错误：交给上层熔断与提示处理。"""
    pass


def _amap_get(path: str, params: dict, action: str = "请求") -> dict:
    """统一的高德 HTTP GET 封装：
    - 前后各打印一条 info 日志
    - 8 秒超时
    - 网络错误 → logger.error + 抛出 AmapNetworkError（上层熔断会捕获）
    - 业务错误（status!=1）→ 抛出 RuntimeError
    """
    url = f"{AMAP_BASE_URL}{path}"
    logger.info(f"[amap] 开始请求高德：{action} url={url} params_keys={sorted(params.keys())}")
    try:
        resp = requests.get(url, params=params, timeout=AMAP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        err_type = _classify_error(e)
        logger.error(
            f"[amap] 高德请求失败：{action} error_type={err_type} "
            f"exception_class={e.__class__.__name__} detail={str(e)[:300]}"
        )
        raise AmapNetworkError(f"地图服务暂时无法访问（{err_type}）")
    logger.info(f"[amap] 收到高德响应：{action} status={data.get('status')} info={data.get('info')}")
    if str(data.get("status")) != "1":
        raise RuntimeError(
            f"高德返回业务错误: {data.get('info')} (infocode={data.get('infocode')})"
        )
    return data


def _geocode(addr: str, key: str, city_hint: Optional[str] = None) -> str:
    """把中文地址地理编码为经纬度 'lng,lat'。如果传入的本身就是经纬度则直接返回。"""
    addr = addr.strip()
    # 已经是 "lng,lat" 格式
    if "," in addr:
        parts = addr.split(",", 1)
        try:
            float(parts[0]); float(parts[1])
            return addr
        except ValueError:
            pass

    def _call(city: Optional[str]):
        params = {"key": key, "address": addr}
        if city:
            params["city"] = city.strip()
        try:
            d = _amap_get("/geocode/geo", params, action=f"地理编码 {addr}")
        except AmapNetworkError:
            # 地理编码网络失败上抛，由工具层处理
            raise
        geocodes = d.get("geocodes") or []
        if geocodes:
            return geocodes[0].get("location", "")
        return ""

    try:
        loc = _call(city_hint)
    except AmapNetworkError:
        raise
    if not loc and city_hint:
        loc = _call(None)
    if not loc:
        raise RuntimeError(f"无法将地址「{addr}」地理编码为经纬度，请确认地址或换成更明确的POI名称。")
    return loc


@tool
@circuit_break(
    tool_name="amap_poi_search",
    retry_hint="请检查 keywords（必填的搜索关键词）和 city（城市名，建议必填）参数是否正确传入，再试一次；若再次失败请在方案中注明「未能通过地图核实」并继续回复。",
    error_prefix="商户搜索失败",
)
def amap_poi_search(
    keywords: str = Field(
        ...,
        description='搜索关键词，必填（字符串，不可为空）。例如"团建基地"、"轰趴馆"、"本帮菜餐厅"、"长兴岛郊野公园"。',
    ),
    city: Optional[Union[str, list]] = Field(
        default=None,
        description='城市名称或高德 adcode，例如"上海"、"北京市"。用户已提供出发/目的地城市时必须传入；不传将在全国范围搜索，结果可能不精准。',
    ),
    citylimit: bool = Field(
        default=False,
        description="是否严格限定在 city 范围内搜索，布尔值，默认 false；传了 city 时建议设为 true。",
    ),
    offset: int = Field(
        default=5,
        description="每页返回的 POI 数量，整数，1~25 之间，默认 5。方向选项阶段建议设为 3~5 控制调用量。",
    ),
    page: int = Field(
        default=1,
        description="分页页码，整数，从 1 开始，默认 1。",
    ),
) -> str:
    """按关键词和城市搜索地点或商户（高德地图 POI 关键词搜索接口 /v3/place/text）。
    用于确认商户/场地/餐厅/景点是否存在，获取其名称、地址、电话、评分、人均消费、营业时间、经纬度等信息。
    调用本工具时必须至少传入 keywords 参数（搜索关键词，必填字符串，不可以留空），建议同时传入 city 参数。"""
    if not keywords or not str(keywords).strip():
        raise ValueError("keywords（搜索关键词）为必填参数，不能为空，请传入具体关键词如\"团建基地\"。")
    key = _get_api_key()
    city_s = _normalize_city(city)
    params: dict = {
        "key": key,
        "keywords": str(keywords).strip(),
        "offset": max(1, min(int(offset), 25)),
        "page": max(1, int(page)),
        "extensions": "all",
    }
    if city_s:
        params["city"] = city_s
        params["citylimit"] = "true" if citylimit else "false"
    data = _amap_get("/place/text", params, action=f"POI搜索 keywords={keywords} city={city_s}")
    pois = data.get("pois") or []
    if not pois:
        return f"高德地图搜索结果：在{city_s or '全国'}范围内未找到与「{keywords}」匹配的地点。"
    out = [f"高德 POI 搜索结果（关键词：{keywords}，城市：{city_s or '全国'}，共 {len(pois)} 条）："]
    for i, poi in enumerate(pois, 1):
        name = poi.get("name", "")
        addr = poi.get("address", "") or "(未填地址)"
        ptype = poi.get("type", "")
        tel = poi.get("tel", "") or "(未提供)"
        rating = ""
        cost = ""
        open_time = ""
        biz = poi.get("biz_ext") or {}
        if isinstance(biz, dict):
            rating = biz.get("rating", "") or ""
            cost = biz.get("cost", "") or ""
        deep = poi.get("deep_info") or {}
        if isinstance(deep, dict):
            rating = rating or deep.get("rating", "") or ""
            open_time = deep.get("open_time", "") or ""
        extra = []
        if rating:
            extra.append(f"评分:{rating}")
        if cost:
            extra.append(f"人均:{cost}元")
        if open_time:
            extra.append(f"营业时间:{open_time}")
        extra_str = f"（{' / '.join(extra)}）" if extra else ""
        location = poi.get("location", "")
        out.append(
            f"{i}. {name}｜地址：{addr}｜类型：{ptype}｜电话：{tel}{extra_str}"
            + (f"｜经纬度：{location}" if location else "")
        )
    return "\n".join(out)


@tool
@circuit_break(
    tool_name="amap_weather",
    retry_hint="请检查 city 参数是否为正确的中文城市名（如\"上海\"），再试一次；若再次失败请在风险预案中注明「天气待确认」并继续回复。",
    error_prefix="天气查询失败",
)
def amap_weather(
    city: Optional[Union[str, list]] = Field(
        default=None,
        description='查询天气的城市名称或高德 adcode，必填字符串，例如"上海"、"北京"。',
    ),
    extensions: str = Field(
        default="base",
        description='天气类型字符串："base" 返回实况天气（温度、天气、风向、湿度）；"all" 返回未来 4 天预报。默认 "base"。',
    ),
) -> str:
    """查询城市天气（高德地图天气查询接口 /v3/weather/weatherInfo）。
    活动日期在未来几天内时调用本工具查询天气并据此调整风险预案；日期较远时只需在方案中说明季节一般气候即可，不必查询。
    调用时必须传入 city 参数（中文城市名，必填字符串）。"""
    key = _get_api_key()
    city_s = _normalize_city(city)
    if not city_s:
        raise ValueError("city（城市）为必填参数，不能为空。")
    ext = "base" if str(extensions).lower() != "all" else "all"
    params = {"key": key, "city": city_s, "extensions": ext}
    data = _amap_get("/weather/weatherInfo", params, action=f"天气查询 city={city_s} ext={ext}")
    lives = data.get("lives") or []
    forecasts = data.get("forecasts") or []
    if ext == "base" and lives:
        lv = lives[0]
        return (
            f"【{lv.get('city','')} 实况天气】发布时间 {lv.get('reporttime','')}："
            f"天气{lv.get('weather','')}，气温{lv.get('temperature','')}℃，"
            f"{lv.get('winddirection','')}风{lv.get('windpower','')}级，湿度{lv.get('humidity','')}%。"
        )
    if ext == "all" and forecasts:
        fc = forecasts[0]
        casts = fc.get("casts") or []
        lines = [f"【{fc.get('city','')} 未来天气预报】（发布时间 {fc.get('reporttime','')}）"]
        for c in casts:
            lines.append(
                f"- {c.get('date','')}（周{c.get('week','')}）：白天{c.get('dayweather','')} {c.get('daytemp','')}℃ "
                f"{c.get('daywind','')}风{c.get('daypower','')}级；"
                f"夜间{c.get('nightweather','')} {c.get('nighttemp','')}℃ "
                f"{c.get('nightwind','')}风{c.get('nightpower','')}级"
            )
        return "\n".join(lines)
    return f"未获取到 {city_s} 的天气数据。"


@tool
@circuit_break(
    tool_name="amap_route_distance",
    retry_hint="请检查 origin/destination 是否为中文地址或经纬度、必要时传入 origin_city/destination_city，再试一次；若再次失败请写车程估计值并注明「车程待确认」继续回复。",
    error_prefix="车程计算失败",
)
def amap_route_distance(
    origin: str = Field(
        ...,
        description='出发地，必填字符串。支持两种格式：1) 经纬度 "lng,lat"（如"121.4737,31.2304"）；'
        '2) 中文地址或POI名称（如"上海人民广场"、"长兴岛郊野公园"）。中文地址建议同时传 origin_city。',
    ),
    destination: str = Field(
        ...,
        description='目的地，必填字符串。格式同 origin；支持经纬度或中文地址/POI名称。中文地址建议同时传 destination_city。',
    ),
    city: Optional[Union[str, list]] = Field(
        default=None,
        description='默认城市，可选字符串。当出发地和目的地在同一城市时可以只传此项（如"上海"），省略 origin_city/destination_city。',
    ),
    origin_city: Optional[Union[str, list]] = Field(
        default=None,
        description='出发地所在城市（中文城市名或 adcode），可选字符串；origin 为中文地址且与目的地城市不同时建议传入。',
    ),
    destination_city: Optional[Union[str, list]] = Field(
        default=None,
        description='目的地所在城市（中文城市名或 adcode），可选字符串；destination 为中文地址且与出发地城市不同时建议传入。',
    ),
) -> str:
    """计算出发地到目的地的驾车距离和预计时长（高德地图驾车距离测量接口 /v3/distance）。
    用于判断地点候选是否超出用户可接受的车程。
    调用时必须传入 origin（出发地）和 destination（目的地）两个参数（都是字符串，不可为空），
    中文地址建议同时传入 city 或 origin_city/destination_city。"""
    if not origin or not str(origin).strip():
        raise ValueError("origin（出发地）为必填参数，不能为空。")
    if not destination or not str(destination).strip():
        raise ValueError("destination（目的地）为必填参数，不能为空。")
    key = _get_api_key()
    city_s = _normalize_city(city)
    o_city = _normalize_city(origin_city) or city_s
    d_city = _normalize_city(destination_city) or city_s
    o_loc = _geocode(str(origin).strip(), key, o_city)
    d_loc = _geocode(str(destination).strip(), key, d_city)
    params = {"key": key, "origins": o_loc, "destination": d_loc, "type": "1"}
    data = _amap_get("/distance", params, action=f"驾车距离 origin={origin} -> destination={destination}")
    results = data.get("results") or []
    if not results:
        raise RuntimeError("未返回距离数据。")
    r0 = results[0]
    distance_m = float(r0.get("distance", 0))
    duration_s = float(r0.get("duration", 0))
    km = distance_m / 1000.0
    minutes = duration_s / 60.0
    o_city_str = f"{o_city} " if o_city else ""
    d_city_str = f"（{d_city}）" if d_city else ""
    return (
        f"驾车路线：{origin} {o_city_str}→ {destination}{d_city_str}\n"
        f"距离：约 {km:.2f} 公里；预计车程：约 {minutes:.1f} 分钟（高德地图常规路况估算，不含实时拥堵/节假日等因素）。"
    )
