"""Reference text served as yoto://docs/interactive."""

INTERACTIVE = """\
# Yoto interactive cards: events, config and device behaviour

Each fact is marked **observed** (seen on a real player or through the API) or **assumed**
(from Yoto's docs or community notes, not yet checked by us).

## Events (on a track)

| Event | When | Command |
| --- | --- | --- |
| `onEnd` | the track finishes | `{"cmd": "goto", "params": {"chapterKey", "trackKey"}}` or `{"cmd": "stop"}` |
| `onLhb` | left button pressed during the track | goto |
| `onRhb` | right button pressed during the track | goto |

- A `goto` to a chapter or track key that doesn't exist fails silently: the player just stops.
  The compiler and simulator refuse those. (**observed** in the Hilo work)
- After a track ends with `stop`, the player still answers the buttons of that track. A choice
  prompt relies on this. (**observed**: Hilo Books 4-8 (Choose a Book))
- `goto` lands on `trackKey` inside `chapterKey`. Keys must be unique on the card. (**observed**)

## Config (`content.config`)

| Key | Meaning | Status |
| --- | --- | --- |
| `playbackType: "interactive"` (on `content`) | events drive playback | observed |
| `autoadvance: "none"` | don't play the next chapter by itself | observed |
| `disableTrackNav` / `disableChapterNav: false` | the dial can still skip | observed (dial skipping works) |
| `resumeTimeout` | seconds the player keeps a resume point; default 2592000 (30 days) | observed |
| `resumeTimeout: 1` | the card starts over every time | observed on a real player |
| `resumeTimeout: 0` | undocumented; `yoto-mcp check resume` tests whether Yoto accepts it | assumed |
| `onlineOnly: false` | plays offline when all audio is Yoto-hosted (`yoto:#`) | assumed |

## Chapter order is dial order

Turning the dial moves through chapters in card order, whatever the events say. Put each
announcement right before the chapter it introduces, and the start node first. (**observed**)

## Media

- `yoto:#<id>` refs belong to the account that uploaded them. A card that uses another account's
  ref is rejected with 400 "Media file not found". Copy media with `yoto_media_import`. (**observed**)
- Icons and covers belong to one account too. (**observed**)
- Icons: 16×16 PNG (RGBA, no palette) or GIF. Upload with autoConvert=false to keep the pixels exact.
  Whether Yoto returns the same pixels with alpha is what `yoto-mcp check icons` tests. (**assumed**)

## Patterns

- **Book selector**: choice per book; right cycles, left announces the book and plays to the end.
  Segments step tracks with left/right. `yoto_template_book_selector`.
- **Linear with title tracks**: a spoken title chapter before each book, and an intro that names the
  track where each book starts. `yoto_template_title_tracks`.
- **Branching story**: a choice at each fork (left/right), segments between, end nodes.
- **Quiz**: a question choice; the right answer goes on, the wrong one plays a "try again" segment
  whose `next` returns to the question.
"""
