# Key智API ImageGen Skill

Key智API image generation/editing Skill for Codex.

- API base URL: `https://aitokey.top/v1`
- Skill name: `keyzhi-imagegen`
- Default model: `gpt-image-2` (availability on Key智API is **not** verified)
- Python standard library only; no bundled MCP server.

## Install in Codex

Use Codex's built-in skill installer and replace `<OWNER>` with the GitHub account name:

```text
$skill-installer install https://github.com/<OWNER>/keyzhi-imagegen
```

Alternatively, ask Codex: “Install the skill from https://github.com/<OWNER>/keyzhi-imagegen using $skill-installer.”

Restart Codex if the installed skill is not detected immediately.

## Configure your API key

Recommended: set `KEYZHI_API_KEY` in your environment. On Windows PowerShell for the current session:

```powershell
$env:KEYZHI_API_KEY = "YOUR_KEYZHI_API_KEY"
```

To persist without putting secrets into the repo, create the local file `%APPDATA%\KeyZhiAPI\settings.json`:

```json
{
  "base_url": "https://aitokey.top/v1",
  "api_key": "YOUR_KEYZHI_API_KEY"
}
```

**Never commit real API keys.** Do not put credentials into a public Codex chat or git commit.

## Usage

```powershell
python scripts/keyzhi_images.py generate --prompt "A futuristic city" --output "result.png"
```

For editing, see [SKILL.md](SKILL.md). The upstream must support image generation for requests to work.

## Publish to GitHub

1. Create a GitHub repository named `keyzhi-imagegen` (public if you want anyone to install).
2. Upload the **contents of this folder** to the repository root; ensure `SKILL.md` is at root.
3. Share `https://github.com/<OWNER>/keyzhi-imagegen` with Codex.
