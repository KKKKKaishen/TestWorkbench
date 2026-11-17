"""本地 OpenAI 兼容 mock 服务：验证本地大模型链路（测试用，监听 127.0.0.1:18999）。
用法：python _mock_openai.py   然后在设置页把本地端点指到 http://127.0.0.1:18999/v1
"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if not self.path.endswith("/chat/completions"):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        prompt = ""
        for m in body.get("messages", []):
            prompt += m.get("content", "")
        # 骨架生成场景回一个最简 pytest 脚本；其余场景回通用确认文本
        if "Playwright" in prompt or "pytest" in prompt or "骨架" in prompt:
            content = (
                "from playwright.sync_api import Page, expect\n\n\n"
                "def test_generated_by_local_llm(page: Page):\n"
                '    page.goto("https://example.com")\n'
                '    expect(page).to_have_title("Example Domain")\n'
            )
        else:
            content = "本机模型已就绪（mock）。"
        resp = {
            "id": "mock-1", "object": "chat.completion", "model": body.get("model", "mock-7b"),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }
        data = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        print("[mock-llm]", fmt % args)


if __name__ == "__main__":
    print("mock OpenAI server on http://127.0.0.1:18999/v1")
    HTTPServer(("127.0.0.1", 18999), Handler).serve_forever()
