"""Compatibility entrypoint for the corrected structural L4 evaluator."""
import evaluate_structural_rotation_rollfix as _impl

globals().update(
    {
        name: getattr(_impl, name)
        for name in dir(_impl)
        if not name.startswith("__")
    }
)

if __name__ == "__main__":
    _impl.main()
