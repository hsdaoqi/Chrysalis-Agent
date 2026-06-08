"""PyInstaller 入口：electron_runtime 已拆为包，这里提供单文件入口给打包器。"""
from chrysalis.electron_runtime import main

if __name__ == "__main__":
    main()
