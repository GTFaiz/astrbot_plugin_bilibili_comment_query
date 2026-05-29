import asyncio
import json
import traceback
from typing import Any, Dict, List, Optional

import aiohttp

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star
from astrbot.api import logger
from astrbot.api import AstrBotConfig

# HTML 模板，使用 Jinja2 语法渲染评论数据
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
    body {
        font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif;
        padding: 20px;
        background: #f7f8fa;
        color: #333;
    }
    .header {
        text-align: center;
        margin-bottom: 20px;
    }
    .username {
        font-size: 24px;
        font-weight: bold;
        margin: 0;
    }
    .all-names {
        font-size: 14px;
        color: #999;
        margin-top: 5px;
    }
    .comment-card {
        background: white;
        border-radius: 10px;
        padding: 14px;
        margin-bottom: 12px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.04);
        border-left: 4px solid #00a1d6;
    }
    .video-title {
        font-weight: bold;
        font-size: 15px;
        color: #1a1a1a;
        margin-bottom: 6px;
    }
    .comment-content {
        font-size: 15px;
        line-height: 1.6;
        margin: 8px 0;
        color: #444;
    }
    .meta {
        display: flex;
        gap: 15px;
        font-size: 12px;
        color: #999;
    }
    .error-msg {
        text-align: center;
        font-size: 18px;
        color: #e74c3c;
        margin-top: 40px;
    }
</style>
</head>
<body>
{% if error %}
    <div class="error-msg">{{ error }}</div>
{% else %}
    <div class="header">
        <div class="username">{{ current_name }}</div>
        <div class="all-names">曾用名：{{ all_names }}</div>
    </div>
    {% for item in comments %}
    <div class="comment-card">
        <div class="video-title">{{ item.title }}</div>
        <div class="comment-content">{{ item.content }}</div>
        <div class="meta">
            <span>{{ item.pubdate }}</span>
            <span>👍 {{ item.favorite }}</span>
            <span>💬 {{ item.reply }}</span>
            <span>UP: {{ item.video_owner_name }}</span>
        </div>
    </div>
    {% endfor %}
{% endif %}
</body>
</html>
"""

class BiliCommentPlugin(Star):
    """AstrBot 插件：查询 B 站指定 UID 用户的最近评论，并以图片形式展示。"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        logger.info("B站评论查询插件已加载。")

    @filter.command("uid")
    async def query_comment(self, event: AstrMessageEvent) -> MessageEventResult:
        """
        /uid <UID> [页码] [每页条数]
        查询 B 站用户最近评论，支持分页。
        """
        # 1. 解析命令参数
        parts = event.message_str.strip().split()
        if len(parts) < 2:
            yield event.plain_result("❌ 参数不足，请按格式：/uid <UID> [页码] [每页条数]")
            event.stop_event()
            return

        try:
            uid = int(parts[1])
            if uid <= 0:
                raise ValueError
        except ValueError:
            yield event.plain_result("❌ UID 必须为合法的正整数。")
            event.stop_event()
            return

        # 页码和每页条数（优先使用命令输入，否则用配置文件默认值）
        page = 1
        page_size = self.config["page"]["default_page_size"]

        if len(parts) >= 3:
            try:
                page = int(parts[2])
                if page < 1:
                    raise ValueError
            except ValueError:
                yield event.plain_result("❌ 页码必须为正整数。")
                event.stop_event()
                return

        if len(parts) >= 4:
            try:
                page_size = int(parts[3])
                max_page_size = self.config["page"]["max_page_size"]
                if page_size < 1 or page_size > max_page_size:
                    yield event.plain_result(f"❌ 每页条数应在 1～{max_page_size} 之间。")
                    event.stop_event()
                    return
            except ValueError:
                yield event.plain_result("❌ 每页条数必须为正整数。")
                event.stop_event()
                return

        # 2. 构造 API 请求 URL
        base_url = self.config["api"]["base_url"].rstrip("/")
        api_url = f"{base_url}/get_replies?uid={uid}&pageSize={page_size}&pageNum={page}&keyword=&start_dt=&end_dt="
        timeout_sec = self.config["api"]["timeout"]
        proxy = self.config["proxy"] if self.config.get("proxy") else None

        logger.info(f"查询 UID={uid} 的评论：page={page}, page_size={page_size}")

        # 3. 发送异步网络请求
        try:
            timeout = aiohttp.ClientTimeout(total=timeout_sec)
            connector = None
            if proxy:
                connector = aiohttp.TCPConnector(force_close=True)
                session_kwargs = {"connector": connector}
            else:
                session_kwargs = {}

            async with aiohttp.ClientSession(timeout=timeout, **session_kwargs) as session:
                async with session.get(api_url, proxy=proxy) as resp:
                    if resp.status != 200:
                        yield event.plain_result(f"❌ API 请求失败，HTTP 状态码：{resp.status}")
                        event.stop_event()
                        return
                    try:
                        data = await resp.json()
                    except Exception:
                        yield event.plain_result("❌ 服务器返回的数据格式异常，无法解析。")
                        event.stop_event()
                        return
        except asyncio.TimeoutError:
            yield event.plain_result("❌ 请求超时，请稍后重试。")
            event.stop_event()
            return
        except aiohttp.ClientError as e:
            logger.error(f"网络请求错误：{e}")
            yield event.plain_result("❌ 网络连接失败，请检查网络或代理设置。")
            event.stop_event()
            return
        except Exception as e:
            logger.error(traceback.format_exc())
            yield event.plain_result("❌ 未知错误，请联系管理员。")
            event.stop_event()
            return

        # 4. 数据处理
        if not isinstance(data, dict):
            yield event.plain_result("❌ 返回数据结构异常。")
            event.stop_event()
            return

        # 有些 API 会在 code 字段标记错误
        if data.get("code") and data.get("code") != 0:
            err_msg = data.get("msg", "API 内部错误")
            yield event.plain_result(f"❌ 查询失败：{err_msg}")
            event.stop_event()
            return

        current_name = data.get("current_name", f"UID:{uid}")
        all_names_raw = data.get("all_names", "")
        all_names_list: List[str] = []
        if all_names_raw:
            try:
                # all_names 可能是 JSON 数组字符串，也可能本身就是列表
                if isinstance(all_names_raw, str):
                    all_names_list = json.loads(all_names_raw)
                elif isinstance(all_names_raw, list):
                    all_names_list = all_names_raw
            except json.JSONDecodeError:
                all_names_list = [all_names_raw.strip()] if all_names_raw else []
        all_names_str = "、".join(all_names_list) if all_names_list else "暂无记录"

        comments: List[Dict[str, Any]] = data.get("data", [])
        if not comments:
            yield event.plain_result(f"用户 {current_name} 暂无评论数据。")
            event.stop_event()
            return

        # 提取必要字段（做安全截断，防止 HTML 注入）
        def safe_str(s: Any) -> str:
            if isinstance(s, str):
                # 替换 HTML 特殊字符，防止模板渲染异常
                return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
            return str(s)

        clean_comments = []
        for c in comments:
            clean_comments.append({
                "title": safe_str(c.get("title", "未知视频")),
                "content": safe_str(c.get("content", "")),
                "pubdate": safe_str(c.get("pubdate", ""))[:19],
                "favorite": str(c.get("favorite", 0)),
                "reply": str(c.get("reply", 0)),
                "video_owner_name": safe_str(c.get("video_owner_name", ""))[:30],
            })

        # 5. HTML 渲染为图片
        render_data = {
            "current_name": safe_str(current_name),
            "all_names": all_names_str,
            "comments": clean_comments,
            "error": None
        }
        render_timeout = self.config["render"]["timeout"] * 1000  # 毫秒

        try:
            image_url = await self.html_render(
                HTML_TEMPLATE,
                render_data,
                options={
                    "full_page": True,
                    "timeout": render_timeout,
                    "scale": "device",  # 高分屏渲染
                }
            )
            if image_url:
                yield event.image_result(image_url)
            else:
                yield event.plain_result("❌ 图片渲染失败，未返回图片地址。")
        except Exception as e:
            logger.error(f"图片渲染异常：{traceback.format_exc()}")
            yield event.plain_result("❌ 图片生成失败，请检查 T2I 服务是否可用。")

        event.stop_event()

    async def terminate(self):
        """插件卸载时的清理工作。"""
        logger.info("B站评论查询插件已卸载。")