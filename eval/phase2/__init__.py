"""Gate G2 (docs/PLAN.md section 5, docs/specs/eval.md 5.9 and T2.10): the Indian video layer.

* ``rows``      the per-category table (which truth targets, which detector, which target)
* ``dataset``   reads the T2.1 manifest (images, clips, per-frame truth)
* ``raster``    polygon coverage helpers
* ``detectors`` loads whatever the vision registry discovers; a missing or broken detector is NOT RUN, never a pass
* ``frames``    one frame's masks against its truth -> compact records
* ``timeline``  which results paint which clip frame (the engine's nearest-result rule), flicker series
* ``scoring``   pure aggregation: recall / precision / Wilson CI per category and level, flicker, latency
* ``offline``   the offline accuracy leg (CPU, no stream)
* ``perf``      the real-time leg with every detector on (fps, delay, CPU, VRAM)
* ``report``    ``eval/reports/<date>-phase2.md`` and its JSON twin

Entry points: ``python -m eval.run phase2 --leg preflight|offline|perf`` and ``phase2-report``; the gate is
``scripts/gate_phase2.sh``.
"""
