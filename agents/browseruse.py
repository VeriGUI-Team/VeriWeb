import asyncio
import os
from dotenv import load_dotenv

import json
import re
from tqdm import tqdm
from browser_use import Agent
from browser_use.llm import ChatOpenAI

load_dotenv()

model = "o3"
api_key = ""
base_url = ""
DATA_FILE = "./dataset.json"

tasks: list[tuple[str, str]] = []        # [(folder_name, instruct), ...]

def natural_key(name: str):
    return [int(s) if s.isdigit() else s.lower()
            for s in re.split(r'(\d+)', name)]

try:
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        raw_tasks = json.load(f)
except (FileNotFoundError, json.JSONDecodeError) as e:
    raise RuntimeError(f"read {DATA_FILE} failed：{e}")


for item in sorted(raw_tasks, key=lambda x: natural_key(x["folder"])):
    folder_name = item.get("folder")
    instruct = item.get("instruct", "").strip()

    if not folder_name or not instruct:
        tqdm.write(f"⚠️  skipped：{item}")
        continue

    tasks.append((folder_name, instruct))

async def main() -> None:
    if not tasks:
        print("❌ not found")
        return

    llm = ChatOpenAI(model=model, api_key=api_key, base_url=base_url)

  
    for folder_name, task_text in tqdm(tasks, desc="Running tasks", unit="task"):
        save_dir = f"./result/{model}/{folder_name}"
        file_system_path = f"./result/{model}/{folder_name}/workplace"
        if os.path.exists(save_dir):
            tqdm.write(f"⚠️  Skip “{task_text}” — {save_dir} existed")
            continue

        os.makedirs(save_dir, exist_ok=True)

        task_text += "\nPlease save a markdown file (result.md) about the final answer."


        agent = Agent(
            task=task_text,
            llm=llm,
            save_conversation_path=save_dir,
            file_system_path=file_system_path,
        )
        await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
