"""
AI工具 - 集成DeepSeek API
"""
import asyncio
import ssl
import aiohttp
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


async def get_ai_raw_reply(context_text: str, config: Dict[str, Any], 
                           is_mention: bool = False) -> Optional[str]:
    """
    获取AI原始回复
    
    Args:
        context_text: 上下文文本
        config: AI配置
        is_mention: 是否被@提及
    
    Returns:
        AI生成的回复文本
    """
    api_key = config.get('ai_key', '')
    if not api_key or "sk-" not in api_key:
        logger.warning("No valid API key configured")
        return None
    
    system_prompt = config.get('system_prompt', '')
    forbidden_words = config.get('forbidden_words', [])
    
    # 构建违禁词列表
    forbidden_str = ",".join(forbidden_words) if forbidden_words else ""
    
    # 构建系统提示
    mention_hint = " (注意：有群友在 @ 你，请针对性回复)" if is_mention else ""
    
    # 动态注入违禁词
    if "(见配置)" in system_prompt:
        system_prompt = system_prompt.replace("(见配置)", forbidden_str)
    elif forbidden_str and "严禁词汇" not in system_prompt:
        system_prompt += f" 严禁提及: {forbidden_str}"
    
    # 合并完整系统提示
    full_prompt = f"{system_prompt}{mention_hint}"
    
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": full_prompt},
            {"role": "user", "content": context_text}
        ],
        "max_tokens": config.get('ai_max_length', 60),
        "temperature": 1.5,
        "presence_penalty": 1.5,
        "frequency_penalty": 1.0
    }
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    try:
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        
        async with asyncio.timeout(10):
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=ssl_ctx)
            ) as session:
                async with session.post(
                    'https://api.deepseek.com/chat/completions',
                    json=payload,
                    headers=headers
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data['choices'][0]['message']['content']
                    else:
                        error_text = await resp.text()
                        logger.error(f"API Error {resp.status}: {error_text}")
                        return None
    except asyncio.TimeoutError:
        logger.error("AI request timeout")
        return None
    except Exception as e:
        logger.error(f"AI request failed: {e}")
        return None
