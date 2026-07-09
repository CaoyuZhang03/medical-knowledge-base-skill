#!/usr/bin/env python3
from kb import main

if __name__ == "__main__":
    raise SystemExit(main(["schedule", *(__import__("sys").argv[1:])]))
