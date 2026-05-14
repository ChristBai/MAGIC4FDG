"""Allow running the pipeline as: python3 -m src.pipeline"""

import os
os.environ.setdefault("LANGCHAIN_OPENAI_TCP_KEEPALIVE", "0")

from src.pipeline.supervisor import main

if __name__ == "__main__":
    main()
