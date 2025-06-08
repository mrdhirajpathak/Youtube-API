# main.py
# YouTube Downloader API with Advanced Features
# Built with FastAPI and yt-dlp

import os
import secrets
import shutil
import json
import time
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, Depends, HTTPException, status, BackgroundTasks, Header
from fastapi.responses import JSONResponse, FileResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from typing import Dict, List, Optional
import yt_dlp
import asyncio

# --- Configuration ---
# Render.com पर इन्हें Environment Variables के रूप में सेट करना सबसे अच्छा है।
MASTER_API_KEY = os.environ.get("MASTER_API_KEY", "your-super-secret-master-key")
API_KEY_FILE = "api_keys.json"
TEMP_DOWNLOAD_DIR = "temp_downloads"
MAX_FILE_AGE_SECONDS = 3600  # 1 घंटा

# --- FastAPI App Initialization ---
app = FastAPI(
    title="Advanced YouTube Downloader API",
    description="An advanced API to download video/audio from YouTube with API key management, rate limiting, and format selection. Inspired by robust commercial APIs.",
    version="1.0.0",
    contact={
        "name": "Your Name",
        "url": "http://your-website.com",
        "email": "your-email@example.com",
    },
    license_info={
        "name": "MIT License",
    },
)

# --- Models for Request and Response ---
class VideoRequest(BaseModel):
    url: str = Field(..., description="The YouTube video URL.")
    quality: Optional[str] = Field("best", description="Desired video quality (e.g., '1080p', '720p', 'best').")

class AudioRequest(BaseModel):
    url: str = Field(..., description="The YouTube video URL.")
    bitrate: Optional[int] = Field(192, description="Desired audio bitrate in kbps (e.g., 128, 192, 256).")

class InfoResponse(BaseModel):
    id: str
    title: str
    description: str
    duration: int
    thumbnail: str
    uploader: str
    view_count: int
    formats: List[Dict]

class APIKeyData(BaseModel):
    key: str
    owner: str
    requests_per_minute: int
    is_active: bool = True
    total_requests: int = 0
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    last_used: Optional[str] = None

class NewAPIKeyRequest(BaseModel):
    owner: str = Field(..., description="The name of the key owner for identification.")
    requests_per_minute: int = Field(10, description="Rate limit for the new key.")

# --- API Key Management & Rate Limiting ---
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)
api_keys_db: Dict[str, APIKeyData] = {}
rate_limit_tracker: Dict[str, List[float]] = {}

def save_keys_to_file():
    """Saves the current API key database to a JSON file."""
    with open(API_KEY_FILE, "w") as f:
        json.dump({key: data.dict() for key, data in api_keys_db.items()}, f, indent=4)

def load_keys_from_file():
    """Loads API keys from the JSON file into memory."""
    if not os.path.exists(API_KEY_FILE):
        return
    try:
        with open(API_KEY_FILE, "r") as f:
            data = json.load(f)
            for key, value in data.items():
                api_keys_db[key] = APIKeyData(**value)
    except (json.JSONDecodeError, TypeError):
        print(f"Warning: Could not parse {API_KEY_FILE}. Starting with an empty key database.")

async def get_api_key(api_key: str = Depends(api_key_header)):
    """Dependency to validate the API key and perform rate limiting."""
    if api_key not in api_keys_db:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API Key. Please provide a valid key in the 'X-API-Key' header."
        )

    key_data = api_keys_db[api_key]
    if not key_data.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This API Key is inactive."
        )

    # Rate Limiting Logic
    current_time = time.time()
    if api_key not in rate_limit_tracker:
        rate_limit_tracker[api_key] = []

    # Filter out requests older than 60 seconds
    rate_limit_tracker[api_key] = [t for t in rate_limit_tracker[api_key] if current_time - t < 60]

    if len(rate_limit_tracker[api_key]) >= key_data.requests_per_minute:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit of {key_data.requests_per_minute} requests per minute exceeded."
        )

    rate_limit_tracker[api_key].append(current_time)
    
    # Update usage stats
    key_data.total_requests += 1
    key_data.last_used = datetime.utcnow().isoformat()
    # In a real-world scenario, you might want to save this periodically, not on every request.
    # For this example, we'll skip saving on every request to avoid file I/O bottleneck.

    return key_data

# --- Helper Functions ---
def create_temp_dir():
    """Creates the temporary download directory if it doesn't exist."""
    os.makedirs(TEMP_DOWNLOAD_DIR, exist_ok=True)

async def cleanup_temp_files():
    """Periodically cleans up old files from the temporary directory."""
    while True:
        await asyncio.sleep(600)  # Run every 10 minutes
        try:
            now = time.time()
            for filename in os.listdir(TEMP_DOWNLOAD_DIR):
                file_path = os.path.join(TEMP_DOWNLOAD_DIR, filename)
                if os.path.isfile(file_path):
                    if os.stat(file_path).st_mtime < now - MAX_FILE_AGE_SECONDS:
                        os.remove(file_path)
                        print(f"Cleaned up old file: {filename}")
        except Exception as e:
            print(f"Error during file cleanup: {e}")


# --- App Startup and Shutdown Events ---
@app.on_event("startup")
async def startup_event():
    """Actions to perform on application startup."""
    print("Starting up the API server...")
    create_temp_dir()
    load_keys_from_file()
    # Add the master key if it doesn't exist
    if not any(k for k, v in api_keys_db.items() if v.owner == "master"):
        master = APIKeyData(
            key=MASTER_API_KEY,
            owner="master",
            requests_per_minute=100, # High limit for master key
            is_active=True
        )
        api_keys_db[MASTER_API_KEY] = master
        save_keys_to_file()
        print("Master API key has been configured.")
    
    # Start the background task for cleaning up files
    asyncio.create_task(cleanup_temp_files())
    print("API is ready to accept requests.")

@app.on_event("shutdown")
def shutdown_event():
    """Actions to perform on application shutdown."""
    print("Shutting down... saving API keys.")
    save_keys_to_file()


# --- API Endpoints ---
@app.get("/", tags=["General"])
async def root():
    """A welcome message and basic API information."""
    return {
        "message": "Welcome to the YouTube Downloader API",
        "documentation": "/docs",
        "features": {
            "video_download": "/download/video",
            "audio_download": "/download/audio",
            "video_info": "/info",
            "api_key_management": "/admin/keys"
        }
    }

@app.post("/info", response_model=InfoResponse, tags=["Downloader"])
async def get_video_info(request: VideoRequest, api_key: APIKeyData = Depends(get_api_key)):
    """
    Retrieves detailed metadata for a given YouTube URL.
    """
    ydl_opts = {'quiet': True, 'no_warnings': True, 'skip_download': True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(request.url, download=False)
            
            formats = []
            for f in info.get('formats', []):
                formats.append({
                    "format_id": f.get('format_id'),
                    "ext": f.get('ext'),
                    "resolution": f.get('resolution'),
                    "fps": f.get('fps'),
                    "filesize": f.get('filesize'),
                    "filesize_approx": f.get('filesize_approx'),
                    "vcodec": f.get('vcodec'),
                    "acodec": f.get('acodec'),
                    "url": f.get('url') # Note: These URLs are often temporary
                })

            return InfoResponse(
                id=info.get('id'),
                title=info.get('title'),
                description=info.get('description'),
                duration=info.get('duration'),
                thumbnail=info.get('thumbnail'),
                uploader=info.get('uploader'),
                view_count=info.get('view_count'),
                formats=formats
            )
    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YouTube URL or video is unavailable. Error: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {e}")


@app.post("/download/video", tags=["Downloader"])
async def download_video(request: VideoRequest, background_tasks: BackgroundTasks, api_key: APIKeyData = Depends(get_api_key)):
    """
    Downloads a YouTube video based on the provided URL and quality.
    This returns a direct file response.
    """
    video_id = ""
    try:
        # Extract video ID to create a unique filename
        with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
            info = ydl.extract_info(request.url, download=False)
            video_id = info['id']
    except Exception:
        raise HTTPException(status_code=400, detail="Could not parse video URL.")

    # Generate a unique filename to prevent conflicts
    unique_filename_base = f"{video_id}_{secrets.token_hex(4)}"
    output_template = os.path.join(TEMP_DOWNLOAD_DIR, f"{unique_filename_base}.%(ext)s")
    
    # yt-dlp options for video download
    # We choose a format that has both video and audio. 'best' is a good default.
    quality_selector = f"bestvideo[height<={request.quality.replace('p','')}]" if request.quality != 'best' else 'best'
    ydl_opts = {
        'format': f'{quality_selector}[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': output_template,
        'quiet': True,
        'no_warnings': True,
        'merge_output_format': 'mp4',
    }

    try:
        # This can be a long process, so it's good practice to run it in a threadpool
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(ydl_opts).download([request.url]))

        # Find the downloaded file
        downloaded_file = None
        for file in os.listdir(TEMP_DOWNLOAD_DIR):
            if file.startswith(unique_filename_base):
                downloaded_file = os.path.join(TEMP_DOWNLOAD_DIR, file)
                break
        
        if not downloaded_file:
            raise HTTPException(status_code=500, detail="Download completed, but the output file was not found.")
        
        # Schedule the file for deletion after the response is sent
        background_tasks.add_task(os.remove, downloaded_file)

        return FileResponse(path=downloaded_file, media_type='video/mp4', filename=os.path.basename(downloaded_file))

    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(status_code=400, detail=f"Failed to download video. It might be private or region-locked. Error: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An internal server error occurred during download: {e}")


@app.post("/download/audio", tags=["Downloader"])
async def download_audio(request: AudioRequest, background_tasks: BackgroundTasks, api_key: APIKeyData = Depends(get_api_key)):
    """
    Downloads only the audio from a YouTube video and returns it as an MP3 file.
    """
    video_id = ""
    try:
        with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
            info = ydl.extract_info(request.url, download=False)
            video_id = info['id']
    except Exception:
        raise HTTPException(status_code=400, detail="Could not parse video URL.")

    unique_filename_base = f"{video_id}_audio_{secrets.token_hex(4)}"
    output_template = os.path.join(TEMP_DOWNLOAD_DIR, f"{unique_filename_base}.%(ext)s")

    # yt-dlp options for audio extraction to mp3
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_template,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': str(request.bitrate),
        }],
        'quiet': True,
        'no_warnings': True,
    }

    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(ydl_opts).download([request.url]))
        
        final_filepath = os.path.join(TEMP_DOWNLOAD_DIR, f"{unique_filename_base}.mp3")

        if not os.path.exists(final_filepath):
             raise HTTPException(status_code=500, detail="Audio conversion failed or file not found.")

        background_tasks.add_task(os.remove, final_filepath)
        
        return FileResponse(path=final_filepath, media_type='audio/mpeg', filename=os.path.basename(final_filepath))

    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(status_code=400, detail=f"Failed to download audio. Error: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An internal server error occurred: {e}")


# --- Admin Endpoints for API Key Management ---
def is_master(api_key: str = Depends(api_key_header)):
    """Dependency to check if the provided key is the master key."""
    if api_key != MASTER_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to access this resource."
        )
    return True

@app.post("/admin/keys/generate", response_model=APIKeyData, tags=["Admin"], dependencies=[Depends(is_master)])
async def generate_api_key(request: NewAPIKeyRequest):
    """
    [MASTER KEY ONLY] Generates a new API key.
    """
    new_key = f"ytapi_{secrets.token_urlsafe(24)}"
    key_data = APIKeyData(
        key=new_key,
        owner=request.owner,
        requests_per_minute=request.requests_per_minute
    )
    api_keys_db[new_key] = key_data
    save_keys_to_file()
    return key_data

@app.get("/admin/keys", response_model=List[APIKeyData], tags=["Admin"], dependencies=[Depends(is_master)])
async def list_api_keys():
    """
    [MASTER KEY ONLY] Lists all generated API keys.
    """
    return list(api_keys_db.values())

@app.put("/admin/keys/{key_to_update}/toggle", tags=["Admin"], dependencies=[Depends(is_master)])
async def toggle_api_key_status(key_to_update: str):
    """
    [MASTER KEY ONLY] Activates or deactivates an API key.
    """
    if key_to_update not in api_keys_db:
        raise HTTPException(status_code=404, detail="API Key not found.")
    if key_to_update == MASTER_API_KEY:
        raise HTTPException(status_code=400, detail="Cannot deactivate the master key.")
        
    api_keys_db[key_to_update].is_active = not api_keys_db[key_to_update].is_active
    save_keys_to_file()
    status = "active" if api_keys_db[key_to_update].is_active else "inactive"
    return {"message": f"API key for '{api_keys_db[key_to_update].owner}' is now {status}."}

@app.delete("/admin/keys/{key_to_delete}", tags=["Admin"], dependencies=[Depends(is_master)])
async def delete_api_key(key_to_delete: str):
    """
    [MASTER KEY ONLY] Deletes an API key.
    """
    if key_to_delete not in api_keys_db:
        raise HTTPException(status_code=404, detail="API Key not found.")
    if key_to_delete == MASTER_API_KEY:
        raise HTTPException(status_code=400, detail="Cannot delete the master key.")

    deleted_key_owner = api_keys_db[key_to_delete].owner
    del api_keys_db[key_to_delete]
    save_keys_to_file()
    return {"message": f"Successfully deleted API key for owner: '{deleted_key_owner}'."}


# To run this app locally for testing:
# uvicorn main:app --reload
