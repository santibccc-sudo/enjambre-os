# Third-party notices

enjambre's Python code has one runtime dependency, installed from PyPI rather than bundled:

| Package | License | Project |
|---|---|---|
| PyYAML | MIT | https://github.com/yaml/pyyaml |

The dashboard ships one bundled browser library, `src/enjambre/web/vendor/3d-force-graph.min.js`
(version 1.73.4), loaded only when the Memory tab opens. It is the standalone build of
**3d-force-graph** by Vasco Asturiano (MIT, https://github.com/vasturiano/3d-force-graph), which
includes these packages:

| Package | License |
|---|---|
| three | MIT |
| three-forcegraph | MIT |
| three-render-objects | MIT |
| kapsule | MIT |
| accessor-fn | MIT |
| float-tooltip | MIT |
| data-bind-mapper | MIT |
| d3-force-3d | MIT |
| d3-octree | MIT |
| d3-array, d3-scale, d3-scale-chromatic | ISC |
| ngraph.forcelayout, ngraph.graph | BSD-3-Clause |
| @tweenjs/tween.js | MIT |
| polished | MIT |
| tinycolor2 | MIT |
| lodash-es | MIT |

Full license texts are in each project's repository. The versions bundled are the ones
3d-force-graph 1.73.4 resolved when it was built.
