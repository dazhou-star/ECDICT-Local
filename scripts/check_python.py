import sys

if sys.version_info < (3, 10):
    print("需要 Python 3.10 或更高版本，当前版本：", sys.version.split()[0])
    raise SystemExit(1)

print("Python 版本：", sys.version.split()[0])
