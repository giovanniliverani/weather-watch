---
name: sync-readme-to-notion
description: Syncs this repository's README.md to the Extreme Weather Watch Notion page. Use when the user asks to sync the README to Notion, update Notion from markdown, push docs to Extreme Weather Watch, or run /sync-readme-to-notion.
---

# Sync README to Notion

Push `README.md` to the Extreme Weather Watch Notion page. Do not pull Notion into the README unless the user explicitly asks.

## Target

- Local file: `README.md` at the repo root
- Notion page URL: `https://app.notion.com/p/ae500a29123340c1ab4afde5088110c1`
- Notion page ID: `ae500a29123340c1ab4afde5088110c1`
- Page title: Extreme Weather Watch
- Icon: 🌤️

## Workflow

1. Authenticate Notion MCP (`plugin-notion-workspace-notion`) if the namespace reports `needsAuth`.
2. Fetch `notion://docs/enhanced-markdown-spec` with Notion `fetch` before writing content. Do not guess Notion markdown.
3. Read `README.md`.
4. Fetch the Notion page and inspect current content, child pages, and databases.
5. Convert GitHub markdown to Notion-flavored markdown:
   - Treat the README `#` title as the Notion page title property, not body content.
   - Start body content at `## Introduction` (or the first heading after the title).
   - Keep headings, lists, links, and wording. Do not rewrite or expand the spec.
   - Use Markdown links for external URLs.
6. Update Notion with `replace_content` on page ID `ae500a29123340c1ab4afde5088110c1`:
   - `new_str`: converted body (no duplicate `# Extreme Weather Watch` heading)
   - `allow_async`: true
   - Preserve child pages/databases with `<page url="...">` / `<database url="...">` tags from fetch if any exist.
   - If replace would delete children, show the list and ask before setting `allow_deleting_content`.
7. If the README H1 changed, also `update_properties` with `title`. Keep icon `🌤️`.
8. Poll `get_async_task` if the update returns an async task, then fetch the page and confirm the body matches the README.
9. Reply with the Notion URL and a short confirmation. Do not commit unless asked.
