---
status: accepted
---

# A frequent Hermes cron tick wakes Atlas, and Atlas alone decides what is due

The brief says a missed Daily Brief arrives late the same day, a missed day is skipped, and a Pause silences Atlas while filing continues (W Q4, W Q17). Hermes cron behaves differently on all three counts. Its default catch-up runs a missed daily job the next morning. A job's script receives no slot time. A paused job that is resumed fires its elapsed slot at once. We decided setup creates one no-agent cron job, every 10 minutes with catch-up off, whose script runs `hermes atlas tick`. The tick sends the Daily Brief only when all of these hold: Atlas is not paused now, the owner's set time has passed today in the owner's timezone, and no brief file for today exists. The Sunday Review follows the same rule. A Pause is Atlas data (`pauses.md`), and Atlas never pauses the Hermes job.

## Considered Options

- **One cron job per message at the owner's time** (the Hermes prototype's recommendation, with "Pause pauses the job"). Rejected for three reasons. Catch-up sends yesterday's brief. Changing the time needs a cron edit. A resumed job fires its elapsed slot.
- **A timer inside the gateway.** Rejected, because Hermes offers no supported periodic task or startup hook for plugins.

## Consequences

- The brief time, the Pause and the date guard all live in one pure function (`schedule.due`). A brief is up to 10 minutes late.
- Collection and filing also run on this tick, so a Session is filed within about 10 minutes.
