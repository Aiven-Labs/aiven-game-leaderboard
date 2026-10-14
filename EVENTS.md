# Running an event leaderboard

Every event gets its own leaderboard, so several can run on the same dates. You never need
a developer or a deploy to set one up.

## Set up a new event (2 minutes)

1. Open the score server's **`/admin`** page and sign in.
2. Scroll to **Events**. Fill in:
   - **slug**: a short lowercase id with hyphens and a year, e.g. `kafka-summit-2027`
     (it appears in links, so keep it tidy; the year stops next year's event mixing in)
   - **name**: what players see, e.g. "Kafka Summit 2027"
   - **opens / closes** (optional, UTC): plays outside the window still save, but are not
     put on the event's board. Leave blank for always open.
3. Click **Save event**. The new row shows everything you need:

   | You want | Use |
   |---|---|
   | Players on their own phones (slide deck, poster) | **Game link** or its **QR code** (`https://inkless.dev/e/<slug>`) |
   | A big screen or a "see where you rank" slide | **Leaderboard link** or its QR code |
   | An arcade cabinet | **Cabinet URL**: set the cabinet browser's start page to it, once |

   QR codes download as PNG or SVG (SVG stays sharp on a projector).

## During and after the event

- The **leaderboard page** has an event picker; `…/leaderboard?event=<slug>` links straight to one board.
- In `/admin`, use the **event filter** to see just that event's entrants. **Export CSV** and
  **Export claimed only** follow the filter, so the prize draw uses that event's list only.
- Junk or test entries: **Edit** or delete them in the table before drawing.

## Good to know

- A game is tagged with its event when it is *played*, from the link it was started from.
  Nothing is inferred from dates, which is why two events can overlap.
- A typo'd or unknown event slug does no harm: the score is saved with no event and shows on
  no event board.
- Anyone who knows an event's link can play into it. For a raffle, draw from the CSV and
  remove stragglers first.
- Nicknames only have to be unique within an event.
