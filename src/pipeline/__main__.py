"""Pipeline 模块入口：支持 python3 -m src.pipeline 方式运行。"""

import os
os.environ.setdefault("LANGCHAIN_OPENAI_TCP_KEEPALIVE", "0")

from src.pipeline.supervisor import main

if __name__ == "__main__":
    main()
