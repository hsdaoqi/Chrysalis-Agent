# 桌面端使用指南

## 启动
```powershell
cd desktop-electron
npm install
npm run dev
```

## 打包分发
```powershell
.\scripts\build_desktop_electron.ps1 -InstallNodeDeps
```

打包产物默认输出到：

```text
desktop-electron\dist\release\ChrysalisDesktop-*-portable.exe
```
