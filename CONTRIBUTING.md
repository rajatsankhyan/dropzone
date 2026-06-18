# Contributing to DropZone

Thanks for taking the time to contribute! Here's everything you need to know.

## Getting started

```bash
git clone https://github.com/rajatsankhyan/dropzone
cd dropzone
pip3 install -r requirements.txt
python3 menubar.py   # Mac
python3 systray.py   # Windows
python3 app.py       # Any OS (terminal mode)
```

## How to contribute

1. **Fork** the repo
2. **Create a branch** — `git checkout -b feature/your-feature-name`
3. **Make your changes**
4. **Test it** — run the app and verify your change works on at least one platform
5. **Open a PR** — describe what you changed and why

## What we welcome

- Bug fixes
- Windows / Linux compatibility improvements
- Performance improvements
- UI improvements on the mobile or laptop page
- New features that fit the core idea (local, private, fast)

## What to avoid

- Adding cloud dependencies or external API calls — everything must stay local
- Breaking the mobile UI on iPhone or Android
- Adding new pip dependencies without good reason

## Code style

- Python: follow the existing style, no strict linter enforced
- Keep it simple — this is a ~1200 line single-file server, not a framework
- No comments explaining *what* the code does — only *why* if it's non-obvious

## Reporting bugs

Open a GitHub issue with:
- What you expected to happen
- What actually happened
- Your OS and Python version
- Steps to reproduce

## Questions

Open an issue or reach out to [Rajat Sankhyan](https://github.com/rajatsankhyan) directly.
