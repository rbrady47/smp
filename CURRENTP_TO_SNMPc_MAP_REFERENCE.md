# SMP Operational Map Reference

This document captures the SNMPc-style operational map direction that we discussed, without replacing the current topology feature yet.

## Intent

Keep the current topology map as the active feature for now.

Use this document as the reference for evolving that feature toward a more operator-authored, SNMPc-style network map over time.

## Core Product Direction

The future map should behave more like an operational network map than an auto-generated topology diagram.

The goal is:

- let the user build a custom visual map for situational awareness
- bind live SMP data to map objects and links
- support drill-down into unit-level submaps
- preserve the current discovery/topology truth separately from authored presentation

## Recommended Model

Treat SMP topology as two related but different capabilities:

1. Discovery Topology
- auto-derived from Anchor Node and Discovered Node data
- used for engineering truth, discovery validation, and system reasoning

2. Operational Map
- operator-authored visual map
- used for mission/ops situational awareness
- bound to live node and relationship data

This means the current topology feature should remain the authoritative discovery-oriented view, while gradually gaining operational-map capabilities.

## Authoring Model

The operational map should start as a blank canvas.

Users should be able to add:

- node objects
- submap objects
- label objects

Each object should have connection points so links can attach cleanly and remain stable when objects move.

## Node Object Behavior

When a node object is placed on the map:

1. it starts as an unbound visual object
2. the user assigns a node ID
3. once assigned, a small approved set of bindable status fields becomes available

Likely first bindings for node objects:

- Ping
- Status
- Site Name
- Unit
- Version

Later hover/detail fields could include:

- TX
- RX
- RTT
- Tunnel status
- Last seen

## Link Behavior

Links should connect two object connection points.

After creating a link, the user should be able to bind the link to a status from one endpoint or, later, from a relationship/tunnel model.

Likely first bindings for links:

- RTT
- Link status
- Tunnel health summary

Later hover/detail fields could include:

- RTT
- TX
- RX
- tunnel 1-4 status

## Submaps

Submaps are a key requirement.

Expected behavior:

- the top map can show major operational structure such as Anchor Nodes or units
- a submap object drills into another map view
- unit maps should start with a blank canvas as well
- submaps should include a built-in Back button by default

Example:

- main map shows anchor nodes and unit entry points
- clicking a unit submap opens that unit’s map
- the unit map shows the local nodes, links, labels, and local operational context

## Data Binding Philosophy

Do not make the system an unrestricted “bind anything to anything” rules engine.

Instead, use a controlled catalog of approved fields and visual targets.

Good early visual targets:

- object primary status
- object secondary text
- object badge
- link color
- link label
- hover summary

This keeps the system flexible without overengineering it.

## Suggested Evolution Path

Build onto the current topology feature in small steps:

1. keep the current topology/discovery view intact
2. add optional authored layout capability to that feature
3. add map objects and object placement
4. add node assignment and status binding
5. add link creation and link binding
6. add submap drill-in
7. add hover summaries and richer operational overlays

## Architectural Guidance

Separate:

- what exists on the network
- how the operator chooses to display it

That means:

- Node Dashboard / discovery remains the source of truth
- topology/discovery remains the validation view
- operational map state stores layout, labels, links, and bindings

This separation will make the feature more adaptable as SMP grows.

## Practical Recommendation

Do not replace the current topology map all at once.

Instead:

- keep improving the existing topology feature
- use this SNMPc-style design as the target direction
- fold the authored-map concepts into the current topology experience gradually

## Summary

The SNMPc-style concept is still a good direction, but it should be treated as the next stage of the current topology capability rather than a separate replacement product.

That gives SMP:

- a stable discovery truth view now
- a clear path to a richer operational map later
- less feature churn
- easier adoption by users
