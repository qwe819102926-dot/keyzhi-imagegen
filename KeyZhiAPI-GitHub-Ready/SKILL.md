---
name: keyzhi-imagegen
description: Generate or edit raster images through the configured Key智API OpenAI-compatible image API. For natural-language image generation and editing requests. Not for SVG or code-native graphics.
---

# Key智API ImageGen

This Skill generates or edits raster images through **Key智API** (`https://aitokey.top/v1`). Invoke it when image generation or image editing is requested. The API must actually support the selected image model and endpoint; changing the URL alone does not add image-generation capability.

## Routing

- If a **configured and verified** compatible image MCP tool is available, it may be used. This ZIP does not contain or install an MCP server.
- Otherwise, use the bundled Python client at `scripts/keyzhi_images.py`.
- Keep the user's requested subject, style, composition, text, model, size, quality, background, output format, aspect ratio, resolution, and image count.
- Use at most three outputs; default model is `gpt-image-2` unless overridden by the user. Ask only for a missing required reference image in edit requests.

## Python direct client

The client first tries the nonstandard async endpoint (`/v1/images/generations/async` or `/v1/images/edits/async`) and polls `/v1/images/tasks/{task_id}`. It falls back to the standard synchronous OpenAI-compatible images endpoint only if the async route explicitly reports 404, 405, or 501.

Generate:

```powershell
python "<skill-directory>\scripts\keyzhi_images.py" generate --prompt "A precise image prompt" --output "C:\path\result.png"
```

Edit:

```powershell
python "<skill-directory>\scripts\keyzhi_images.py" edit --image "C:\path\reference.png" --prompt "Describe the requested change" --output "C:\path\edited.png"
```

For multiple reference images, repeat `--image`; use `--mask` for masked edits. Supported options: `--model`, `--n` (1–3), `--size`, `--quality`, `--background`, `--output-format`, `--aspect-ratio`, `--resolution`, `--timeout`.

On success, return the saved image paths. On failure, report the actual API error; do not silently switch to an unrelated provider.

## Key智API configuration

- **API root (default):** `https://aitokey.top/v1`
- **API key:** `KEYZHI_API_KEY`
- **Override API root:** `KEYZHI_BASE_URL`
- **Override settings path:** `KEYZHI_SETTINGS_PATH`
- **Default settings file (Windows):** `%APPDATA%\KeyZhiAPI\settings.json`
- **Optional model detection overrides:** `KEYZHI_IMAGE_MODELS`

Credentials are read from `KEYZHI_API_KEY` first and then the local settings JSON `api_key` field. API root is read from `KEYZHI_BASE_URL`, then the local settings JSON `base_url` field, then the default. No fallback to `OPENAI_API_KEY` or old Codexpert credentials is performed.

Example settings:

```json
{
  "base_url": "https://aitokey.top/v1",
  "api_key": "YOUR_KEYZHI_API_KEY"
}
```

Prefer the environment variable to putting a credential on disk. Never print, embed, or share the user's secret API key. The compatibility of the default model `gpt-image-2` with Key智API is not verified.
