# Globe geography

`svg-globe-path.ts` contains a static SVG path generated from Natural Earth's
1:110m land polygons:

- [Source GeoJSON](https://github.com/nvkelso/natural-earth-vector/blob/master/geojson/ne_110m_land.geojson)
- [Public-domain terms](https://www.naturalearthdata.com/about/terms-of-use/)

Every polygon ring is projected with:

```text
x = (longitude + 180) / 360 * 1259
y = (90 - latitude) / 180 * 477
```

`src/utils/terminal-map.ts` applies the same projection to live terminal pins.
Keep both projections in sync if replacing the geography asset. Coordinates
outside latitude ±90 or longitude ±180, blank strings, and missing values are
excluded from the map. Valid zero coordinates are preserved.

`svg-globe-borders.ts` uses the same projection for these Natural Earth layers:

- [Country boundaries](https://github.com/nvkelso/natural-earth-vector/blob/master/geojson/ne_110m_admin_0_boundary_lines_land.geojson)
- [Regional boundaries](https://github.com/nvkelso/natural-earth-vector/blob/master/geojson/ne_110m_admin_1_states_provinces_lines.geojson) (US state lines at this scale)

`GlobeSvg` clips a faint dot pattern and the boundaries to the land shapes.
The dashboard passes SVG units per screen pixel as `textureScale`, keeping
the texture density consistent as the terminal region resizes across desktop and
mobile layouts. Borders use non-scaling strokes. All colors follow the active theme.

Status circles are drawn directly at every terminal's API coordinates in the SVG.
Terminal codes appear above them in 88×46px HTML buttons.
`layoutTerminalMarkers` reserves each button's full rectangular bounds plus an
8px gap and keeps buttons inside the viewport, accounting for SVG letterboxing.
Status circles never move; only nearby codes are
moved to the nearest free space with a connector to the geographic anchor.
The HTML overlay ignores pointer events except on the individual buttons, so
invisible SVG rectangles cannot block neighboring terminals. Placement is stable
across reordered/capacity-only snapshots and recomputes on resize or location change.
Labels also avoid covering the status circles. The dashboard has one automatically
fitted regional view, using the full available width and a taller map canvas.
If a dense viewport has no free position, remaining terminals appear in a separate
selectable row rather than overlapping another button.

The vector geography is bundled locally; no image service or tile server is
needed at runtime. `pulse.tsx` respects reduced motion. `tooltip.tsx` adapts
the provided demo's `render` slot to the shared Radix tooltip's `asChild` API.
