# Ideas

Brainstorming ideas for STAMP. When an idea is promoted to a feature
specification, it should be removed from this list.

- There must be private tickets that will be visible only to certain logged-in users. This will be used mainly for embargoed tickets. During the creation of private tickets, verify that all other ways to access that information (API endpoints) are equally protected. Create unit tests for this and evaluate whether a sub-agent that verifies data protection could also be useful.
- Propose STAMP command-line commands that could be useful
- Some codestreams/packages will need to be tracked even if they are not shipped in any product. For example go1.25
- Once all scheduled tasks (e.g. fetchers) are defined, review all schedule times and spread them out to avoid them all starting at the same moment
