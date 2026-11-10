# Atlas v1 is a catalog plugin for the one machine where Hermes runs

The owner's request was to gather their work "across their machines", and the brief once said Atlas shows one picture across all of them. We decided instead that v1 is an open-source Hermes plugin, submitted to the Hermes plugin catalog, that anyone installs on their main or only machine where Hermes already runs, and that it collects only from that machine. It finds the Harnesses installed there by itself, Hermes included, and the owner adds in its settings the ones it cannot find. A plugin that others install must work on a machine its author has never seen, and collecting from several machines would make every install depend on a setup beyond Hermes itself.

## Considered Options

- **Collect from every machine the owner uses from day one.** Rejected for v1: it needs something running on each machine and a way to ship Sessions between them, which a catalog plugin cannot assume.
- **Back up the other machines' history once, with no collection afterwards.** Rejected: it gives a picture that is complete on day one and wrong a week later, without saying so.

## Consequences

Work done on other machines is missing from the Pursuits, the Journal and the five questions, and Atlas says so rather than presenting a partial answer as complete. Nothing in Atlas may assume the author's own machines, folders or Harnesses. Collecting from other machines is deferred, not rejected.
