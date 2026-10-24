# Publishing these pages as a GitHub wiki

GitHub wikis live in a separate repository, `<owner>/<repo>.wiki.git`. The files
in this directory are written as wiki pages, so links such as
`[Tailscale setup](Tailscale-Setup)` resolve after publishing.

```bash
# Clone the wiki repository (the first page can be created from the GitHub UI).
git clone git@github.com:<owner>/<repo>.wiki.git /tmp/reezyn-wiki

# Copy the pages. PUBLISHING.md is repo guidance, not a wiki page.
for page in Home.md Tailscale-Setup.md Using-the-Phone-Page.md \
            Troubleshooting.md Releasing-and-Notarization.md _Sidebar.md; do
  cp "docs/wiki/$page" /tmp/reezyn-wiki/
done

cd /tmp/reezyn-wiki
git add .
git commit -m "docs: add remote access, Tailscale, and notarization guides"
git push
```

`_Sidebar.md` is rendered as the wiki's navigation. `Home.md` becomes the wiki
home page. Keep both in sync when adding a page.

If the wiki is not enabled yet, open the repository's **Wiki** tab and create the
first page; GitHub then creates the `.wiki.git` repository.
