---
name: rpinode-patterns
description: Guidelines and architectural principles for developing the rpinode project (path management, templating, SSE, persistence, validation).
---

# rpinode Patterns

Use this skill when modifying or extending the `rpinode` project to ensure consistency with its core architectural principles.

## 1. Path Management
All file paths must be derived from `src/core/paths.py`. Never hardcode relative or absolute paths directly in other modules.
- Use `paths.SRC_DIR`, `paths.DATA_DIR`, `paths.TEMPLATES_DIR`, etc.
- Example: `config_file = paths.DATA_DIR / "config.json"`

## 2. "Poupée Russe" Templating
Logic must stay in Python. HTML templates in `templates/` are simple shells with `$variable` placeholders.
- **Pattern**: Render small components first, then inject them into larger ones.
- **Tool**: Use the `render(template_name, **context)` function from `src/web/templating.py`.
- **Example**:
  ```python
  widget = render("widget.html", title="Status", value="OK")
  page = render("home.html", content=widget)
  layout = render("layout.html", body=page)
  ```

## 3. Dynamic Updates (SSE)
Use Server-Sent Events for real-time updates instead of polling.
- **Server**: Implement stream handlers in `src/web/stream.py`.
- **Client**: Use `EventSource` in `static/app.js` to listen for events and update the DOM.

## 4. Persistence & Logs
- All persistent data (JSON, SQLite, logs) must go into the `data/` directory.
- Use the central configuration system in `src/core/config.py`.

## 5. Validation & Deployment
- Always run `./run_tests.sh` before considering a task complete.
- Use `./run.sh` to restart the service; it automatically runs tests and handles the process swap safely with `sudo`.

## 6. Documentation & Context
Always check for additional documentation files to understand specific workflows or business logic.
- Use `find . -type f -iname "*.md"` to locate all documentation files.
- Files like `README.md` and `HOWTO.md` contain critical operational information.

## 7. Shortcuts
When the user says "git" to you, you must perform a git and push.
