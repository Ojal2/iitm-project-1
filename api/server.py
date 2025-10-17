import os
import base64
from datetime import datetime
from fastapi import FastAPI, Request
from pydantic import BaseModel
from typing import List, Optional
from github import Github
import requests
import logging

# ------------------------------
# Logging for Vercel
# ------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

# ------------------------------
# Environment & Config
# ------------------------------
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
if not GITHUB_TOKEN:
    raise RuntimeError("Set the GITHUB_TOKEN environment variable in Vercel")

# SECRET_FORM should be set from Google Form submissions in production
SECRET_FORM = os.environ.get("SECRET_FORM", "Ojal2")  # fallback for local testing

g = Github(GITHUB_TOKEN)
app = FastAPI(title="Captcha Solver API", version="1.0")

# ------------------------------
# Request Models
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
# GitHub Helper Functions
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
            logging.info(f"♻️ Updated file: {attach.filename}")
        except:
            repo.create_file(attach.filename, f"Add {attach.filename}", content)
            logging.info(f"➕ Added file: {attach.filename}")

    # Update README.md with brief and round info
    readme_text = f"# {task_name}\n\n"
    try:
        readme_file = repo.get_contents("README.md")
        existing_readme = readme_file.decoded_content.decode()
        readme_text = existing_readme + f"\n\n## Round {round_index} Updates\n{brief}\nUpdated: {datetime.utcnow().isoformat()}"
        repo.update_file(readme_file.path, f"Update README for Round {round_index}", readme_text, readme_file.sha)
    except:
        readme_text += f"\n\n## Round {round_index} Updates\n{brief}\nUpdated: {datetime.utcnow().isoformat()}"
        repo.create_file("README.md", f"Add README Round {round_index}", readme_text)
    logging.info("📝 README.md updated.")

    # Add MIT License if round 1
    if round_index == 1:
        license_text = "MIT License\n\nCopyright (c) ..."
        try:
            repo.create_file("LICENSE", "Add MIT License", license_text)
        except:
            pass

    # Commit SHA of latest commit
    commit_sha = repo.get_commits()[0].sha
    pages_url = f"https://{user.login}.github.io/{task_name}/"

    return repo.html_url, commit_sha, pages_url

# ------------------------------
# POST to evaluation server
# ------------------------------
def post_evaluation(url, payload, max_retries=5):
    delay = 1
    for _ in range(max_retries):
        try:
            r = requests.post(url, json=payload, headers={"Content-Type": "application/json"})
            if r.status_code == 200:
                return True
        except:
            pass
        import time
        logging.info(f"🔄 Retrying evaluation POST in {delay}s...")
        time.sleep(delay)
        delay *= 2
    logging.warning("⚠️ Failed to POST evaluation after retries")
    return False

# ------------------------------
# API Endpoints
# ------------------------------
@app.get("/")
def root():
    return {"message": "Captcha Solver API running on Vercel!"}

@app.post("/api-endpoint")
async def handle_request(request: Request):
    payload_json = await request.json()
    payload = RequestPayload(**payload_json)

    # Verify secret
    if payload.secret != SECRET_FORM:
        logging.warning(f"❌ Invalid secret from {payload.email}")
        return {"error": "Invalid secret"}

    # Init or update repo
    repo_url, commit_sha, pages_url = init_or_update_repo(payload.task, payload.round, payload.brief, payload.attachments)

    # Respond to evaluation server
    eval_payload = {
        "email": payload.email,
        "task": payload.task,
        "round": payload.round,
        "nonce": payload.nonce,
        "repo_url": repo_url,
        "commit_sha": commit_sha,
        "pages_url": pages_url
    }
    post_evaluation(payload.evaluation_url, eval_payload)

    return {"status": "ok", "repo_url": repo_url, "commit_sha": commit_sha, "pages_url": pages_url}
