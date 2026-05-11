# Base64Batch SaaS

A small FastAPI app that converts uploaded images to Base64 and downloads the result as JSON or Excel.

## Features
- Python virtualenv setup
- MIME validation with `python-magic`
- Per-file and total upload limits
- Rate limiting
- Optional API key auth
- Excel formula injection protection
- Separate JS file for CSP compliance
- Dev and production startup scripts

## Quick Start
```bash
source venv/bin/activate
./run.sh
