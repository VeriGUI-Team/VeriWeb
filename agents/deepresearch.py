import json
import os
import time
from tenacity import retry, stop_after_attempt, wait_exponential
from openai import OpenAI
from tqdm import tqdm
import logging
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s',
    handlers=[
        logging.FileHandler('oai_deepresearch_sync.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Default configuration
base_url = os.environ.get("OPENAI_BASE_URL")
api_key = os.environ.get("OPENAI_API_KEY")
model = "o3-deep-research"
MAX_CONCURRENT = 3  # Lower concurrency for sync version

@dataclass
class ResearchTask:
    id: str
    prompt: str
    status: str = "pending"  # pending, processing, completed, failed
    result: Optional[str] = None
    error: Optional[str] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    retry_count: int = 0
    raw_info: Optional[Dict[str, Any]] = None

class OAISyncDeepResearch:
    """Synchronous OpenAI Deep Research client for processing research tasks.

    This class provides synchronous processing of research tasks using OpenAI's
    deep research model with configurable concurrency and retry mechanisms.
    """

    def __init__(self,
                 base_url: str = base_url,
                 api_key: str = api_key,
                 model: str = model,
                 max_concurrent: int = MAX_CONCURRENT,
                 max_retries: int = 10,
                 retry_delay: float = 1.0):
        """Initialize the research client.

        Args:
            base_url: OpenAI API base URL
            api_key: OpenAI API key (from environment variable)
            model: Model name to use for research
            max_concurrent: Maximum concurrent research tasks
            max_retries: Maximum retry attempts for failed tasks
            retry_delay: Base delay between retries in seconds
        """
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable is required")

        self.client = OpenAI(base_url=base_url, api_key=api_key) if base_url else OpenAI(api_key=api_key)
        self.model = model
        self.max_concurrent = max_concurrent
        self.max_retries = max_retries
        self.retry_delay = retry_delay

        # Progress tracking
        self.completed_count = 0
        self.failed_count = 0
        self.total_count = 0

    def _retry_error_callback(self, retry_state):
        """Callback function for retry attempts that logs warning messages."""
        exception = retry_state.outcome.exception()
        logger.warning(f"Retry attempt {retry_state.attempt_number} failed: {type(exception).__name__} - {str(exception)}")
        return None

    @retry(stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=1, max=15), retry_error_callback=_retry_error_callback)
    def _execute_research(self, task: ResearchTask) -> ResearchTask:
        """Execute a single research task with retry logic.

        Args:
            task: ResearchTask object containing the research prompt

        Returns:
            ResearchTask: The completed research task with results

        Raises:
            Exception: If the research task fails after all retry attempts
        """
        try:
            task.start_time = time.time()
            task.status = "processing"

            logger.info(f"Starting task {task.id}")

            response = self.client.responses.create(
                model=self.model,
                input=task.prompt,
                tools=[
                    {"type": "web_search_preview"},
                    {"type": "code_interpreter", "container": {"type": "auto"}},
                ],
                timeout=5000
            )

            task.result = response.output_text
            task.status = "completed"
            task.end_time = time.time()

            self.completed_count += 1
            logger.info(f"Task {task.id} completed in {task.end_time - task.start_time:.2f} seconds")

            return task

        except Exception as e:
            logger.error(f"Task {task.id} failed: {type(e).__name__} - {str(e)}")
            task.status = "failed"
            task.error = f"{type(e).__name__}: {str(e)}"
            task.end_time = time.time()
            self.failed_count += 1
            raise

    def process_tasks(self, tasks: List[ResearchTask]) -> List[ResearchTask]:
        """Process all research tasks synchronously with controlled concurrency.

        Args:
            tasks: List of ResearchTask objects to process

        Returns:
            List[ResearchTask]: List of completed research tasks with results
        """
        self.total_count = len(tasks)

        logger.info(f"Starting to process {self.total_count} tasks, max concurrency: {self.max_concurrent}")

        results = []

        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=self.max_concurrent) as executor:
            future_to_task = {executor.submit(self._execute_research, task): task for task in tasks}

            for future in tqdm(as_completed(future_to_task), total=len(tasks), desc="Processing research tasks"):
                task = future_to_task[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    logger.error(f"Task {task.id} finally failed: {str(e)}")
                    results.append(task)

                progress = (self.completed_count + self.failed_count) / self.total_count * 100
                logger.info(f"Progress: {progress:.1f}% ({self.completed_count} completed, {self.failed_count} failed)")

        return results

    def save_temp_results(self, results: List[ResearchTask], save_path: str):
        """Save temporary results"""
        temp_file = f"{save_path}_temp.json"

        output_data = []
        for task in results:
            if task:
                output_data.append({
                    "id": task.id,
                    "prompt": task.prompt,
                    "status": task.status,
                    "result": task.result,
                    "error": task.error,
                    "start_time": task.start_time,
                    "end_time": task.end_time,
                    "duration": task.end_time - task.start_time if task.start_time and task.end_time else None,
                    "retry_count": task.retry_count
                })

        with open(temp_file, 'w', encoding='utf-8') as f:
            f.write(json.dumps(output_data, ensure_ascii=False, indent=2))

        logger.info(f"Temporary results saved to {temp_file}")

    def save_final_results(self, results: List[ResearchTask], save_path: str):
        """Save final results"""
        output_data = []
        for task in results:
            if task:
                output_data.append({
                    "id": task["id"],
                    "prompt": task["prompt"],
                    "status": task["status"],
                    "result": task["result"],
                    "error": task["error"],
                    "start_time": task["start_time"],
                    "end_time": task["end_time"],
                    "duration": task["end_time"] - task["start_time"] if task["start_time"] and task["end_time"] else None,
                })

        with open(save_path, 'w', encoding='utf-8') as f:
            f.write(json.dumps(output_data, ensure_ascii=False, indent=2))

        logger.info(f"Final results saved to {save_path}")

def load_prompts(file_path: str) -> List[Dict[str, Any]]:
    """Load prompts from JSON file"""
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)

def create_tasks_from_prompts(prompts: List[Dict[str, Any]]) -> List[ResearchTask]:
    """Create research tasks from prompts data"""
    tasks = []
    for i, prompt_data in enumerate(prompts):
        if isinstance(prompt_data, dict):
            prompt_text = prompt_data.get('instruct', str(prompt_data))
            task_id = prompt_data.get('folder', f"task_{i}")
        else:
            prompt_text = str(prompt_data)
            task_id = f"task_{i}"

        tasks.append(ResearchTask(
            id=task_id,
            prompt=prompt_text,
            raw_info=prompt_data
        ))

    return tasks

def main():
    import argparse

    parser = argparse.ArgumentParser(description="OpenAI Deep Research Sync Tool")
    parser.add_argument("--input", "-i", default="prompts.json",
                       help="Input prompts JSON file path")
    parser.add_argument("--output", "-o", default="research_results.json",
                       help="Output results JSON file path")
    parser.add_argument("--concurrency", "-c", type=int, default=3,
                       help="Maximum concurrent tasks")
    parser.add_argument("--retries", "-r", type=int, default=10,
                       help="Maximum retry attempts")

    args = parser.parse_args()

    researcher = OAISyncDeepResearch(
        max_concurrent=args.concurrency,
        max_retries=args.retries,
        retry_delay=1.0
    )

    try:
        # Load prompts
        logger.info("Loading prompts...")
        prompts = load_prompts(args.input)
        tasks = create_tasks_from_prompts(prompts)

        # Read existing results file to get completed and failed task IDs
        completed_task_ids = set()
        failed_task_ids = set()
        if os.path.exists(args.output):
            try:
                with open(args.output, 'r', encoding='utf-8') as f:
                    existing_results = json.load(f)
                    completed_task_ids = {result['id'] for result in existing_results if result.get('status') == 'completed'}
                    failed_task_ids = {result['id'] for result in existing_results if result.get('status') == 'failed'}
                logger.info(f"Read {len(completed_task_ids)} completed tasks, {len(failed_task_ids)} failed tasks")
            except Exception as e:
                logger.warning(f"Failed to read existing results file: {e}")

        # Filter tasks that need to be retried (uncompleted and failed tasks)
        pending_tasks = [task for task in tasks if task.id not in completed_task_ids]
        logger.info(f"Tasks to process: {len(pending_tasks)} (completed: {len(completed_task_ids)})")

        # Process tasks
        if pending_tasks:
            logger.info(f"Starting to process {len(pending_tasks)} pending tasks")
            new_results = researcher.process_tasks(pending_tasks)

            # Merge results
            if os.path.exists(args.output):
                with open(args.output, 'r', encoding='utf-8') as f:
                    existing_results = json.load(f)
                # Remove existing pending tasks and add new results
                existing_results = [r for r in existing_results if r['id'] not in [task.id for task in pending_tasks]]
                existing_results.extend([{
                    "id": task.id,
                    "prompt": task.prompt,
                    "status": task.status,
                    "result": task.result,
                    "error": task.error,
                    "start_time": task.start_time,
                    "end_time": task.end_time,
                    "duration": task.end_time - task.start_time if task.start_time and task.end_time else None,
                    "retry_count": task.retry_count
                } for task in new_results if task])
                results = existing_results
            else:
                results = [{
                    "id": task.id,
                    "prompt": task.prompt,
                    "status": task.status,
                    "result": task.result,
                    "error": task.error,
                    "start_time": task.start_time,
                    "end_time": task.end_time,
                    "duration": task.end_time - task.start_time if task.start_time and task.end_time else None,
                    "retry_count": task.retry_count
                } for task in new_results if task]
        else:
            logger.info("All tasks completed, no processing needed")
            results = []

        # Save final results
        researcher.save_final_results(results, args.output)

        # Statistics
        completed = sum(1 for task in results if task and task.status == "completed")
        failed = sum(1 for task in results if task and task.status == "failed")

        logger.info(f"Processing completed! Total: {len(results)}, successful: {completed}, failed: {failed}")

    except Exception as e:
        logger.error(f"Main program error: {str(e)}")
        raise

if __name__ == "__main__":
    main()