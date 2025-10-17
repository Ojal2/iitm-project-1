import os
import base64
import json
from fastapi import FastAPI, Request
from pydantic import BaseModel
from typing import List, Optional
from github import Github
from datetime import datetime
import asyncio
import httpx
import logging
import sys

# ------------------------------
# Config
# ------------------------------
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
if not GITHUB_TOKEN:
    raise RuntimeError("Set the GITHUB_TOKEN environment variable")

VALID_SECRET = "Ojal2"  # for local testing
g = Github(GITHUB_TOKEN)
app = FastAPI()

# ------------------------------
# Logging
# ------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

# ------------------------------
# Models
# ------------------------------
class Attachment(BaseModel):
    filename: str
    content: str
    mime_type: Optional[str] = "application/octet-stream"

class RequestPayload(BaseModel):
    email: str
    secret: str
    task: str
    round: int
    nonce: str
    brief: str
    checks: List[str]
    evaluation_url: str
    attachments: List[Attachment] = []

# ------------------------------
# GitHub Helper
# ------------------------------
def init_or_update_repo(task_name: str, round_index: int, brief: str, attachments: List[Attachment]):
    user = g.get_user()
    try:
        repo = user.get_repo(task_name)
        logging.info(f"📂 Found existing repo: {task_name}")
    except:
        if round_index == 1:
            logging.info(f"🆕 Creating repo: {task_name}")
            repo = user.create_repo(task_name, private=False)
        else:
            raise RuntimeError(f"Repo {task_name} does not exist for round {round_index}")

    # Add/update files
    for attach in attachments:
        content = base64.b64decode(attach.content.encode())
        try:
            repo_file = repo.get_contents(attach.filename)
            repo.update_file(repo_file.path, f"Update {attach.filename}", content, repo_file.sha)
            logging.info(f"🔄 Updated file: {attach.filename}")
        except:
            repo.create_file(attach.filename, f"Add {attach.filename}", content)
            logging.info(f"➕ Added file: {attach.filename}")

    # Update README.md with brief
    readme_content = f"# {task_name}\n\nBrief:\n{brief}\n\nUpdated: {datetime.utcnow().isoformat()}"
    try:
        readme_file = repo.get_contents("README.md")
        repo.update_file(readme_file.path, "Update README.md", readme_content, readme_file.sha)
        logging.info("📝 README.md updated.")
    except:
        repo.create_file("README.md", "Add README.md", readme_content)
        logging.info("📝 README.md created.")

    # Add MIT License if round 1
    if round_index == 1:
        license_text = "MIT License\n\nCopyright (c) ..."
        try:
            repo.create_file("LICENSE", "Add MIT License", license_text)
            logging.info("📄 LICENSE added.")
        except:
            pass

    commit_sha = repo.get_commits()[0].sha
    pages_url = f"https://{user.login}.github.io/{task_name}/"
    return repo.html_url, commit_sha, pages_url

# ------------------------------
# Async Evaluation POST
# ------------------------------
async def post_evaluation_async(url, payload):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json=payload)
            if r.status_code == 200:
                logging.info(f"✅ Successfully posted evaluation to {url}")
            else:
                logging.warning(f"⚠️ Evaluation POST returned {r.status_code}")
    except Exception as e:
        logging.error(f"❌ Failed to POST evaluation: {e}")

# ------------------------------
# API Endpoint
# ------------------------------
@app.post("/api-endpoint")
async def handle_request(request: Request):
    payload_json = await request.json()
    payload = RequestPayload(**payload_json)

    # Verify secret
    if payload.secret != VALID_SECRET:
        return {"error": "Invalid secret"}

    # Init or update repo
    repo_url, commit_sha, pages_url = init_or_update_repo(payload.task, payload.round, payload.brief, payload.attachments)

    # Prepare evaluation payload
    eval_payload = {
        "email": payload.email,
        "task": payload.task,
        "round": payload.round,
        "nonce": payload.nonce,
        "repo_url": repo_url,
        "commit_sha": commit_sha,
        "pages_url": pages_url
    }

    # Fire POST in background, don't block response
    asyncio.create_task(post_evaluation_async(payload.evaluation_url, eval_payload))

    # Return immediately
    return {
        "status": "ok",
        "repo_url": repo_url,
        "commit_sha": commit_sha,
        "pages_url": pages_url,
        "message": "Repo updated! Evaluation POST sent in background."
    }

# ------------------------------
# Startup Logging
# ------------------------------
@app.on_event("startup")
async def startup_event():
    logging.info("🚀 Server startup complete! Your API is running.")
    logging.info("📘 Visit http://127.0.0.1:8000/docs for interactive API docs.")
