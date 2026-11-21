# Creative

A creative spec describes the selected layout, approved logos and copy,
and optional video and voice-over references. For a cleared bundle, select
the cached bundle loop and voice-over when available, with a template and
stock copy ready on a miss. The house ad covers auction no-fill.

Warm work can upgrade approved copy, synthesize a voice-over from that copy,
or replace the video layer within its generation budget. The player applies
an upgrade to the same creative. Keep logos, copy and QR codes as player
overlays over the video layer.

## Generation engine (docs/creative-generation.md)

`CreativeBrief` is the one input every copy generator takes: brands, the
**angle** (speech > held product > queue > top segment > category), the
**basket offer for the party size**, names to avoid and stage limits. The hot
path composes cached AI copy or targeted stock copy; the warm path asks a
`CopyWriter` (Claude), runs the **copy guard** and the **quality score**, and
upgrades in place only if the draft is safe and on target. Generation spend is
estimated, capped per hour and justified by the moment's expected revenue.

