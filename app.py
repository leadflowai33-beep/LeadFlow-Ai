def _run():
        try:
            exec(open("leadflow_v9_phase3.py").read(), {"__name__": "__main__"})
        except Exception as e:
            import traceback
            logging.error(f"Pipeline error: {e}")
            logging.error(traceback.format_exc())
        finally:
            _lock.release()
