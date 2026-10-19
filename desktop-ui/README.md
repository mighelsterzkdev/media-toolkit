# Desktop interface development

React and TypeScript frontend for the Python desktop host. Its controls use the
official shadcn/ui New York registry sources, Radix primitives, and Tailwind CSS.
All runtime JavaScript, styles, fonts, and icons are local. It does not make web
requests to inspect or download media: those operations use the Python bridge.

Use Node.js 22.12 or newer and pnpm 11.25.0:

```text
pnpm install --frozen-lockfile
pnpm test
pnpm build
```

The build checks TypeScript, creates the Vite bundle, and copies the production
files into `src/universal_downloader/desktop_assets/`. It also collects license
notices for every installed runtime dependency. The host and portable package
use those prebuilt assets, so end users do not need Node.js or pnpm.

`pnpm dev` serves the frontend on loopback for development. Outside the Python
desktop host, the page displays a connection notice and disables media actions.
Test fixtures inject the bridge only inside automated tests; there is no demo
data in production.

The official component sources were downloaded from
`https://ui.shadcn.com/r/styles/new-york/{component}.json`. Their upstream MIT
license is retained in `SHADCN-LICENSE.txt`. The npm distribution of
`react-remove-scroll-bar` omits its license file, so its upstream MIT text is
retained in `licenses/react-remove-scroll-bar.txt` with its source URL.
