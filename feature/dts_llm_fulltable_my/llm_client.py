"""OpenAI SDK client used by the DTS extraction pipeline."""

from __future__ import annotations

from typing import Any

from openai import OpenAI

import json

class ChatCompletionClient: #llm客户端
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        *,
        timeout: float = 180,
        max_retries: int = 2,
        client: Any = None,
    ) -> None:
        self.model = model
        # TODO 1：
        # SDK的base_url应该是API根地址，例如 https://example.com/v1，
        # 不能是 https://example.com/v1/chat/completions。
        #
        # 可以在这里检查：
        # if base_url.endswith("/chat/completions"):
        #     ...
        suffix = "/chat/completions"
        if base_url.endswith(suffix):
            base_url = base_url[:-len(suffix)]


        # TODO 2：
        # client参数是为了以后测试时注入假客户端。
        # 如果没有传client，就创建OpenAI客户端。
        self.client = client or OpenAI(
            # TODO：填写api_key、base_url、timeout、max_retries
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
        )

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        # TODO 3：
        # 调用：
        # self._client.chat.completions.create(...)
        #
        # 传入：
        # model=self._model
        # messages=[
        #     {"role": "system", "content": system_prompt},
        #     {"role": "user", "content": user_prompt},
        # ]
        # temperature=0

        #把参数写成字典的形式再传入，这样可以减免类型的验证
        request = {
            "model" : self.model,
            "messages" : [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                }
            ]
        }
        response = self.client.chat.completions.create(**request) #调用接口，至少传入模型和提示词
        # TODO 4：
        # 从response.choices[0].message.content取得文本。
        # content可能是None，此时返回空字符串。
        result = response.choices[0].message.content or ""
        return result

