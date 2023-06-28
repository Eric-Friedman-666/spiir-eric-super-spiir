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

This means that, usually, running the pipeline in production with a `--feature`
enabled should be avoided. The exception is if a code freeze occurs when some
feature is undergoing final testing/review (but we're confident it works fine).
In that case it might make sense to begin the run with the feature disabled,
and turn it on when there has been signoff.

When the feature is considered stable, has been thoroughly tested, and has
completed an LSC review, the `feature` nomenclature should be dropped and the
parameters may be renamed appropriately.


# SPIIR current feature flags

## signal-removal

*Name*: Signal removal

*Feature flag*: `--feature-signal-removal-bg`

*Parameters*: (see `--help` for full details)
 * --feature-signal-removal-bg-threshold

*Short description*: Improve FARs by removing signal-like backgrounds from the
background statistics calculations.

*Relevant merge requests*: lscsoft/spiir!189

*Relevant review slides*:

*Production plan*: Aim to review this during O4a. When the feature flag is
removed, it will be on all the time but can be effectively disabled by settng a
very high threshold.

*History*:
 * (as of 2023-06-27) currently in branch `signal_removal_bg`

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
