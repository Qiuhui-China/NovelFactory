"""
zhipu_client.py — 智谱API封装 v2
- 敏感词预处理 + API拒绝后智能降敏重写
- 自动重试 + token追踪
"""
import time, json, os, re
from datetime import datetime
from openai import OpenAI


class ZhipuClient:
    def __init__(self, config: dict):
        self.config = config
        self.client = OpenAI(
            api_key=config["api"]["key"],
            base_url=config["api"]["base_url"],
        )
        self.resilience = config["resilience"]
        self.sensitive_map = config["resilience"].get("sensitive_replacements", {})
        self.usage_log = {
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "total_tokens": 0,
            "calls": [],
        }

    def _sanitize_prompt(self, text: str) -> str:
        """预处理：替换已知敏感词。"""
        for old, new in self.sensitive_map.items():
            text = text.replace(old, new)
        return text

    def _build_desensitize_prompt(self, original: str) -> str:
        """API拒绝后，生成降敏改写指令。"""
        return (
            "以下内容因为包含敏感表述被系统拦截，请用更委婉安全的方式重写，"
            "保留核心剧情和情感，但避免任何可能触发内容审核的表述。"
            "不要解释你做了什么修改，直接输出改写后的完整内容。\n\n"
            f"原始内容：\n{original}"
        )

    def chat(self, messages: list, model: str = None, temperature: float = 0.78,
             max_tokens: int = 2500, stage: str = "unknown", label: str = "",
             json_mode: bool = False) -> str:
        model = model or self.config["models"]["workhorse"]

        # 预处理敏感词
        for msg in messages:
            if isinstance(msg.get("content"), str):
                msg["content"] = self._sanitize_prompt(msg["content"])

        last_error = None
        for attempt in range(1, self.resilience["max_retries"] + 1):
            try:
                kwargs = dict(model=model, messages=messages,
                              temperature=temperature, max_tokens=max_tokens)
                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                response = self.client.chat.completions.create(**kwargs)
                finish_reason = response.choices[0].finish_reason
                content = response.choices[0].message.content

                # 检测输出被截断（token耗尽）
                if finish_reason == "length":
                    print(f"  ⚠ [{stage}/{label}] 输出被截断(finish_reason=length)，内容可能不完整，建议减少目标字数或拆分任务")

                # 检查是否被内容审核拦截（智谱会返回特定提示）
                if content and ("抱歉" in content[:20] and ("无法" in content[:50] or "不能" in content[:50])):
                    print(f"  ⚠ [{stage}/{label}] 疑似内容审核拦截，尝试降敏改写...")
                    # 用原始prompt的最后一条user消息做降敏改写
                    user_content = messages[-1].get("content", "")
                    desen_prompt = self._build_desensitize_prompt(user_content)
                    response = self.client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": desen_prompt}],
                        temperature=temperature, max_tokens=max_tokens,
                    )
                    content = response.choices[0].message.content
                    usage = response.usage
                else:
                    usage = response.usage

                content = content.strip() if content else ""

                # 记录
                call_record = {
                    "timestamp": datetime.now().isoformat(),
                    "stage": stage, "label": label, "model": model,
                    "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.completion_tokens,
                    "total_tokens": usage.total_tokens,
                }
                self.usage_log["calls"].append(call_record)
                self.usage_log["total_prompt_tokens"] += usage.prompt_tokens
                self.usage_log["total_completion_tokens"] += usage.completion_tokens
                self.usage_log["total_tokens"] += usage.total_tokens

                print(f"  ✓ [{stage}/{label}] {model} | "
                      f"{usage.total_tokens} tok | 累计: {self.usage_log['total_tokens']:,}")

                time.sleep(self.resilience["request_interval"])
                return content

            except Exception as e:
                last_error = e
                wait = self.resilience["retry_delay"] * attempt
                print(f"  ✗ [{stage}/{label}] 第{attempt}次失败: {e}")
                if attempt < self.resilience["max_retries"]:
                    print(f"    等待 {wait}s 后重试...")
                    time.sleep(wait)

        raise RuntimeError(f"API调用失败，已重试{self.resilience['max_retries']}次。最后错误: {last_error}")

    def save_usage_log(self, filepath: str):
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.usage_log, f, ensure_ascii=False, indent=2)
        total = self.usage_log['total_tokens']
        print(f"\n📊 Token消耗: {total:,} (输入{self.usage_log['total_prompt_tokens']:,} + 输出{self.usage_log['total_completion_tokens']:,})")
