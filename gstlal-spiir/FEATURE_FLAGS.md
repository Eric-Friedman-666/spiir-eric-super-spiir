# Feature flags -- introduction

SPIIR is moving towards a 'feature-flag' model of development.

In this model, work is done on feature branches, but these feature branches are
regularly merged back in to the main development branch.  This is true even if
the work in the feature branch is not fully complete, working, or tested
(although it mustn't break the build or imperil the stability of other parts of
SPIIR).

Instead, incomplete features are hidden behind 'feature flags' that disable
them fully when the user doesn't explicitly opt into them when running the
pipeline. Note that for the model to be effective, it is important that all
aspects of the feature are hidden behind its flag.

The purpose of this document is to (a) document some SPIIR conventions for
using feature flags, (b) list all current feature flags, and (c) serve as a
historical record of previous flags.

# SPIIR feature flag conventions

In general, developers should feel free to add new feature-flag--protected
features. There is no desire to encourage a bureaucracy-heavy approach to this.

Every feature should be protected by a boolean command line option in this form:

    --feature-xyz

The name `xyz` should be short, descriptive, and not clash with (or be a prefix
of) any other feature name. You can use a multi-part name (e.g.
`signal-removal`).

Ideally, the entire feature should be protected by this flag: running the
pipeline without the flag should be unchanged.

Additional feature-specific flags and parameters should be prefixed with
`feature-xyz-`. For example, tuning parameters for a `flooble` feature might be
called:

    --feature-flooble-alpha=123.456
    --feature-flooble-beta=1.2e-4
    --feature-flooble-use-wobbly

Generally speaking, feature flags are for hiding features that are not yet
stable or well-tested enough to be run in production.

Ultimately, it's a goal that the version of the pipeline in production has no
`--feature` flags enabled. In practice, given the timelines for reviews makes
this difficult, we don't regard it as a mistake or failure if the final
production configuration has a feature flag enabled. Similarly, in rare cases
a critical bug might be identified in a feature while the pipeline is running
with that feature in production: in such cases it might make more sense to
turn the flag off rather than produce, and review, a new pipeline version.

Nevertheless, it's crucially important that the final proposed production
configuration is reviewed and tested thoroughly.

When the feature is considered stable, has been thoroughly tested, and has
completed an LVK review, the `feature` nomenclature should be dropped and the
parameters may be renamed appropriately.


# SPIIR current feature flags

## best-far

*Name*: Best FAR

*Feature flag*: `--feature-best-far`

*Parameters*: (see `--help` for full details)
 * --feature-best-far-threshold

*Short description*: Improve FARs by selecting the best (lowest) FAR rather 
than the worst (highest) FAR from the three timescales.

*Relevant merge requests*: lscsoft/spiir!195

*Relevant review slides*: 

*Production plan*: Aim to review this during O4a. When the feature flag is 
removed, it will become the default behaviour. It may be tuned by setting
different background collection timescales or a different threshold.

*History*:
 * (as of 2023-09-19) currently in branch `use_best_far`

*Other notes*:
The threshold may be dependent on detector sensitivity.


## signal-removal

*Name*: Signal removal

*Feature flag*: `--feature-signal-removal-bg`

*Parameters*: (see `--help` for full details)
 * --feature-signal-removal-bg-threshold

*Short description*: Improve FARs by removing signal-like backgrounds from the
background statistics calculations.

*Relevant merge requests*: lscsoft/spiir!189, lscsoft/spiir!192, lscsoft/spiir!225

*Relevant review slides*:

*Production plan*: This feature is only intended for BBH, at least for O4b. As such, this feature will remain defaulted to be disabled, but will be enabled in the BBH config.

During O4b, if required, we'd aim to alter signal removal with an additional review of the configs alone. The New SNR threshold could be changed, or the feature as a whole could be enabled/disabled, per source type.

*History*:
 * (2023-06-27) Development in branch `signal_removal_bg`.
 * (2023-07-03) Now feature complete, merged into `spiir-O4-EW-development` ([See MR 148](https://git.ligo.org/lscsoft/spiir/-/merge_requests/148)).
 * (2023-07-10) Added feature flag documentation ([See MR 192](https://git.ligo.org/lscsoft/spiir/-/merge_requests/192)).
 * (2024-02-16) Began external code review.
 * (As of 2024-02-19) Changes are cherry picked to `add-signal-removal-flags`. ([See MR 225](https://git.ligo.org/lscsoft/spiir/-/merge_requests/225)).

*Other notes*:


# SPIIR past feature flags

None so far.


# Template for future feature flags
Copy, paste and replace!

## feature-flag-name

*Name*: Short, human-comprehensible name

*Feature flag*: `--feature-xyz`

*Parameters*: (see `--help` for full details)
 * list all parameters for this feature flag here

*Short description*: A short description of the scientific or computing impact of
this feature flag. Include a link to a paper/technical note/set of slides if
able

*Relevant merge requests*: list all relevant merge requests here

*Relevant review slides*: list links to all review slides here

*Production plan*: A short description of the plan for how this

*History*:
 * (202x-xx-xx) Merged work-in-progress in to `spiir-O4-EW-development`
 * (202x-yy-yy) Now feature complete (see MR 123)
 * (202x-zz-zz) Fixed bugs (see MR 456)
 * (202x-tt-tt) Subject of review call: http://wiki.ligo.org/...

*Other notes*: Anything else of relevance
