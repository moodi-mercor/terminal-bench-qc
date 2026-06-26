def test_service_outcome():
    # Exercises the service's real outcome (the status artifact); the verifier-named
    # module inside the copied service dir is never invoked as the pass signal.
    assert open("/app/out/status.txt").read().strip() == "done"
