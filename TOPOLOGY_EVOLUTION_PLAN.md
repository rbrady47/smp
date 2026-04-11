# Topology Evolution Plan

This document describes how to evolve the current `/topology` feature toward the SNMPc-style operational map concept without creating a separate product path.

## Decision

The standalone `/operational-maps` experience has been removed.

The current `/topology` page remains the active topology feature and will be the place where authored operational-map capabilities are folded in over time.

## Goal

Keep today’s topology/discovery view working while gradually adding operator-authored capabilities that improve mission and network situational awareness.

The target is:

- one topology feature
- discovery truth retained
- operator-authored layout added incrementally
- submaps supported later
- data binding added in controlled steps

## Guiding Principles

1. Discovery truth stays first-class.
The current topology view should continue to reflect what SMP knows from Anchor Nodes and Discovered Nodes.

2. Layout should become optional, not mandatory.
Authored placement should augment topology, not replace discovery-derived understanding.

3. Binding must stay controlled.
Do not turn topology into an unrestricted rules engine. Start with a small catalog of approved fields and visual targets.

4. Submaps are important, but not phase one.
The first win is improving the main topology view. Submaps come after layout and binding basics are stable.

## Recommended Build Sequence

### Phase 1: Strengthen Current Topology

Use the existing `/topology` page and improve it before adding authoring.

Recommended work:

- clean up the topology stage layout and hierarchy
- make unit attribution and discovery grouping easier to read
- improve filtering, focus, and selected-node context
- keep topology tied to the existing discovery payloads

Success criteria:

- operators can understand the network at a glance
- discovery truth remains visible
- no separate topology product path exists

### Phase 2: Add Optional Authored Placement

Add a topology mode that lets the user position visual objects on the existing topology canvas.

Start with:

- labels
- pinned node placements
- optional group boxes or area markers

This should not replace the current topology rendering yet. It should layer on top of it.

Success criteria:

- users can improve readability of the current topology view
- authored layout feels like an enhancement to discovery, not a fork

### Phase 3: Add Node Binding on `/topology`

Allow a placed topology object to bind to a node ID.

Once bound, enable a controlled set of node-driven display fields.

Suggested first node bindings:

- Ping
- Status
- Site Name
- Unit
- Version

Suggested first display targets:

- primary status color
- secondary text
- badge text

Success criteria:

- an operator can place a node-focused visual and bind it to live SMP node data

### Phase 4: Add Link Authoring

Allow topology objects to expose connection points and let users create simple links.

Suggested first link bindings:

- RTT
- link status
- tunnel health summary

Suggested first display targets:

- line color
- line label

Success criteria:

- an operator can visually represent important paths or relationships on the topology page

### Phase 5: Add Hover Summaries

Add lightweight hover/detail summaries for nodes and links.

Potential early hover fields:

- TX
- RX
- RTT
- Ping
- tunnel status
- last seen

Success criteria:

- users can get richer operational context without leaving the topology page

### Phase 6: Add Submaps

Add submap drill-in only after the main topology experience has stable layout, object binding, and link behavior.

Expected behavior:

- unit or grouping objects can drill into submaps
- submaps start with the same topology model and capabilities
- submaps include a built-in Back action

Success criteria:

- the main map can stay high-level while unit-level maps carry richer operational detail

## What To Reuse From The Removed Operational Map Work

The underlying design ideas are still useful:

- blank-canvas authoring concepts
- object types such as node, submap, and label
- connection-point based links
- controlled binding slots
- submap drill-in model

These ideas should be folded into `/topology` only when each one clearly improves the existing feature.

## What Not To Do

- do not reintroduce a separate topology product path unless there is a strong product reason
- do not build a full Visio/draw.io editor inside SMP
- do not let arbitrary rule scripting become the first binding model
- do not sacrifice discovery clarity for visual customization

## Near-Term Recommended Next Step

The next best step is Phase 1 work on the current `/topology` page:

- simplify the stage
- improve hierarchy and readability
- define where authored overlays would eventually live

That gives us a stronger base before we add any more editing behavior.
