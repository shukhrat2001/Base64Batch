import io
import os
import base64
import logging
from pathlib import Path
from typing import List, Optional

import magic
from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, HTTPException, Form, Request, Depends, Response
from fastapi.responses import StreamingResponse, JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from openpyxl import Workbook
from openpyxl.styles import Font
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

# ==============================================================================
# LOAD ENV
# ==============================================================================
load_dotenv()

# ==============================================================================
# SETTINGS HELPERS
# ==============================================================================
def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

def env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        raise RuntimeError(f"Invalid integer for {name}: {raw}")
    if value < minimum:
        raise RuntimeError(f"{name} must be >= {minimum}")
    return value

SETTINGS = {
    "api_key": os.getenv("API_KEY", "").strip(),
    "jwt_secret_key": os.getenv("JWT_SECRET_KEY", "").strip(),
    "enable_auth": env_bool("ENABLE_AUTH", False),
    "max_file_size": env_int("MAX_FILE_SIZE", 10 * 1024 * 1024),
    "max_files_free_tier": env_int("MAX_FILES_FREE_TIER", 5),
    "max_total_upload_size": env_int("MAX_TOTAL_UPLOAD_SIZE", 25 * 1024 * 1024),
    "rate_limit_per_minute": env_int("RATE_LIMIT_PER_MINUTE", 10),
    "host": os.getenv("HOST", "127.0.0.1").strip(),
    "port": env_int("PORT", 8000),
    "allowed_origins": [origin.strip() for origin in os.getenv(
        "ALLOWED_ORIGINS",
        "http://127.0.0.1:8000,http://localhost:8000"
    ).split(",") if origin.strip()],
}

if not SETTINGS["api_key"]:
    raise RuntimeError("API_KEY is required")

if SETTINGS["max_total_upload_size"] < SETTINGS["max_file_size"]:
    raise RuntimeError("MAX_TOTAL_UPLOAD_SIZE must be >= MAX_FILE_SIZE")

# ==============================================================================
# CONSTANTS
# ==============================================================================
ALLOWED_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/bmp",
    "image/webp",
}

# ==============================================================================
# LOGGING
# ==============================================================================
os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("logs/app.log"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# ==============================================================================
# APP
# ==============================================================================
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="Base64Batch API",
    docs_url=None,
    redoc_url=None
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ==============================================================================
# CORS
# ==============================================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=SETTINGS["allowed_origins"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)

# ==============================================================================
# AUTH
# ==============================================================================
security = HTTPBearer(auto_error=False)

async def verify_api_key(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
):
    if not SETTINGS["enable_auth"]:
        return True

    if not credentials or credentials.credentials != SETTINGS["api_key"]:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return True

# ==============================================================================
# SECURITY MIDDLEWARE
# ==============================================================================
@app.middleware("http")
async def request_size_limit(request: Request, call_next):
    # allow some multipart overhead above raw file size cap
    max_body_size = SETTINGS["max_total_upload_size"] + (1024 * 1024)
    content_length = request.headers.get("content-length")

    if content_length:
        try:
            if int(content_length) > max_body_size:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request body too large"}
                )
        except ValueError:
            pass

    return await call_next(request)

@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' https://cdn.tailwindcss.com; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "font-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    )

    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    return response

# ==============================================================================
# HTML / JS
# ==============================================================================
HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Base64Batch</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100 flex items-center justify-center min-h-screen p-4">
    <div class="bg-white p-8 rounded-xl shadow-xl w-full max-w-lg">
        <h1 class="text-2xl font-bold mb-2 text-center text-indigo-600">⚡ Base64Batch</h1>
        <p class="text-center text-gray-500 text-sm mb-6">Secure image → Base64 converter</p>

        <form id="uploadForm" class="space-y-4">
            <div id="dropzone" class="border-2 border-dashed border-gray-300 rounded-lg p-6 text-center cursor-pointer hover:bg-gray-50 transition">
                <p class="text-gray-600">Drag & drop images here, or click to browse</p>
                <input type="file" id="files" multiple class="hidden" accept="image/*">
                <p id="fileCount" class="mt-2 text-sm text-indigo-600 font-medium"></p>
            </div>

            <div>
                <label for="apiKey" class="block text-sm font-medium text-gray-700 mb-1">
                    API Key <span class="text-gray-400">(optional, required only if auth is enabled)</span>
                </label>
                <input
                    type="password"
                    id="apiKey"
                    class="w-full border rounded-lg px-3 py-2"
                    placeholder="Paste API key if needed"
                    autocomplete="off"
                >
            </div>

            <div>
                <label for="format" class="block text-sm font-medium text-gray-700 mb-1">Output format</label>
                <select id="format" class="w-full border rounded-lg px-3 py-2">
                    <option value="excel">Excel (.xlsx)</option>
                    <option value="json">JSON</option>
                </select>
            </div>

            <button type="submit" class="bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-3 rounded-lg w-full font-semibold transition">
                Convert
            </button>
        </form>

        <div id="status" class="mt-4 text-center text-sm"></div>

        <p class="mt-6 text-xs text-gray-400 text-center">
            Free tier: max 5 files, 10MB each, 25MB total
        </p>
    </div>

    <script src="/static/app.js"></script>
</body>
<footer>

  <p>© Base64Batch</p>

  <!-- Buy Sell Startups Badge -->

  <a href=https://buysellstartups.com/listings/base64batch-mp2zn0xo target="_blank" rel="noopener">

    <img src=https://buysellstartups.com/api/badge/base64batch-mp2zn0xo

         alt="For Sale on Buy Sell Startups"

         width="280"

         height="68">

  </a>

</footer>
</html>
"""

JS = r"""
(function () {
    'use strict';

    const form = document.getElementById('uploadForm');
    const fileInput = document.getElementById('files');
    const fileCount = document.getElementById('fileCount');
    const status = document.getElementById('status');
    const dropzone = document.getElementById('dropzone');
    const apiKeyInput = document.getElementById('apiKey');

    function setStatus(message, colorClass) {
        status.className = 'mt-4 text-center text-sm ' + colorClass;
        status.textContent = message;
    }

    function updateFileCount() {
        const count = fileInput.files.length;
        if (count === 0) {
            fileCount.textContent = '';
        } else if (count > 5) {
            fileCount.textContent = count + ' file(s) selected — max 5 allowed on free tier';
            fileCount.className = 'mt-2 text-sm text-red-600 font-medium';
        } else {
            fileCount.textContent = count + ' file(s) selected';
            fileCount.className = 'mt-2 text-sm text-indigo-600 font-medium';
        }
    }

    dropzone.addEventListener('click', function () {
        fileInput.click();
    });

    fileInput.addEventListener('change', updateFileCount);

    dropzone.addEventListener('dragover', function (e) {
        e.preventDefault();
        dropzone.classList.add('border-indigo-500', 'bg-indigo-50');
    });

    dropzone.addEventListener('dragleave', function () {
        dropzone.classList.remove('border-indigo-500', 'bg-indigo-50');
    });

    dropzone.addEventListener('drop', function (e) {
        e.preventDefault();
        dropzone.classList.remove('border-indigo-500', 'bg-indigo-50');
        fileInput.files = e.dataTransfer.files;
        updateFileCount();
    });

    async function parseError(response) {
        try {
            const data = await response.json();
            return data.detail || 'Conversion failed';
        } catch (_) {
            return 'Conversion failed';
        }
    }

    form.addEventListener('submit', async function (e) {
        e.preventDefault();

        const files = fileInput.files;
        const format = document.getElementById('format').value;
        const apiKey = apiKeyInput.value.trim();

        if (files.length === 0) {
            setStatus('Please select at least one image.', 'text-red-600');
            return;
        }

        if (files.length > 5) {
            setStatus('Free tier allows maximum 5 files per request.', 'text-red-600');
            return;
        }

        const formData = new FormData();
        for (let i = 0; i < files.length; i++) {
            formData.append('files', files[i]);
        }
        formData.append('output_format', format);

        const headers = {};
        if (apiKey) {
            headers['Authorization'] = 'Bearer ' + apiKey;
        }

        setStatus('Processing...', 'text-blue-600');

        try {
            const response = await fetch('/api/convert', {
                method: 'POST',
                body: formData,
                headers: headers
            });

            if (!response.ok) {
                throw new Error(await parseError(response));
            }

            let blob;
            let filename;

            if (format === 'excel') {
                blob = await response.blob();
                filename = 'base64_output.xlsx';
            } else {
                const data = await response.json();
                blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
                filename = 'base64_output.json';
            }

            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            a.remove();
            window.URL.revokeObjectURL(url);

            setStatus('✅ Complete', 'text-green-600');
        } catch (err) {
            setStatus('❌ ' + err.message, 'text-red-600');
        }
    });
})();
"""

# ==============================================================================
# HELPERS
# ==============================================================================
def sanitize_filename(filename: str) -> str:
    safe_name = Path(filename or "upload").name
    safe_name = safe_name.replace("/", "_").replace("\\", "_")
    safe_name = "".join(ch if ch.isprintable() else "_" for ch in safe_name)
    safe_name = safe_name.strip() or "upload"
    return safe_name[:255]

def escape_excel(value):
    if isinstance(value, str) and value and value[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value

def validate_image(contents: bytes, filename: str) -> str:
    if not contents:
        raise HTTPException(status_code=400, detail=f"{filename}: empty file")

    try:
        mime = magic.from_buffer(contents, mime=True)
    except Exception as exc:
        logger.error("magic validation failed for %s: %s", filename, exc)
        raise HTTPException(status_code=500, detail="File type validation unavailable")

    if mime not in ALLOWED_MIME_TYPES:
        raise HTTPException(status_code=400, detail=f"{filename}: invalid image type")

    return mime

def generate_excel(rows: List[dict]) -> io.BytesIO:
    wb = Workbook()
    ws = wb.active
    ws.title = "Base64Data"

    header_font = Font(bold=True)

    headers = ["Filename", "MimeType", "SizeBytes", "Base64"]
    ws.append(headers)

    for cell in ws[1]:
        cell.font = header_font

    for item in rows:
        ws.append([
            escape_excel(item["Filename"]),
            escape_excel(item["MimeType"]),
            item["SizeBytes"],
            escape_excel(item["Base64"]),
        ])

    ws.freeze_panes = "A2"
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 20
    ws.column_dimensions["C"].width = 14
    ws.column_dimensions["D"].width = 60

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output

# ==============================================================================
# ROUTES
# ==============================================================================
@app.get("/", response_class=HTMLResponse)
async def home():
    return HTML

@app.get("/static/app.js")
async def app_js():
    return Response(content=JS, media_type="application/javascript")

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/api/convert")
@limiter.limit(f"{SETTINGS['rate_limit_per_minute']}/minute")
async def convert(
    request: Request,
    files: List[UploadFile] = File(...),
    output_format: str = Form("excel"),
    authenticated: bool = Depends(verify_api_key),
):
    del authenticated  # explicit; auth handled by dependency

    output_format = output_format.strip().lower()
    if output_format not in {"excel", "json"}:
        raise HTTPException(status_code=400, detail="Invalid format")

    if len(files) == 0:
        raise HTTPException(status_code=400, detail="No files provided")

    if len(files) > SETTINGS["max_files_free_tier"]:
        raise HTTPException(status_code=429, detail="Free tier exceeded")

    results = []
    total_size = 0
    client_ip = get_remote_address(request)

    for file in files:
        original_name = file.filename or "upload"
        try:
            contents = await file.read(SETTINGS["max_file_size"] + 1)

            if len(contents) > SETTINGS["max_file_size"]:
                raise HTTPException(
                    status_code=413,
                    detail=f"{original_name} exceeds max size"
                )

            total_size += len(contents)
            if total_size > SETTINGS["max_total_upload_size"]:
                raise HTTPException(
                    status_code=413,
                    detail="Total upload size exceeds allowed limit"
                )

            mime = validate_image(contents, original_name)
            safe_name = sanitize_filename(original_name)
            encoded = base64.b64encode(contents).decode("ascii")

            results.append({
                "Filename": safe_name,
                "MimeType": mime,
                "SizeBytes": len(contents),
                "Base64": encoded
            })
        finally:
            await file.close()

    logger.info(
        "Processed request from %s: files=%s format=%s total_size=%s",
        client_ip, len(results), output_format, total_size
    )

    if output_format == "json":
        return JSONResponse(content=results)

    output = generate_excel(results)

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="base64_output.xlsx"'
        }
    )

# ==============================================================================
# MAIN
# ==============================================================================
if __name__ == "__main__":
    import uvicorn

    logger.info(
        "Starting Base64Batch on %s:%s | auth=%s | rate=%s/min",
        SETTINGS["host"],
        SETTINGS["port"],
        SETTINGS["enable_auth"],
        SETTINGS["rate_limit_per_minute"]
    )

    uvicorn.run(
        "main:app",
        host=SETTINGS["host"],
        port=SETTINGS["port"],
        reload=False,
        access_log=True,
        proxy_headers=True,
        timeout_keep_alive=5
    )
