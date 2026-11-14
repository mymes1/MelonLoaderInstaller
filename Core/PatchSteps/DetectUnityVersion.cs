using AssetsTools.NET.Extra;
using MelonLoader.Installer.Core.Diagnostics;
using System;
using System.IO;
using System.IO.Compression;
using UnityVersion = AssetRipper.Primitives.UnityVersion;

namespace MelonLoader.Installer.Core.PatchSteps;

internal class DetectUnityVersion : IPatchStep
{
    public bool Run(Patcher patcher)
    {
        if (patcher.Args.UnityVersion != null && patcher.Args.UnityVersion != UnityVersion.MinVersion)
            return true;

        using FileStream apkStream = new(patcher.Info.OutputBaseApkPath, FileMode.Open, FileAccess.Read, FileShare.Read);
        using ZipArchive archive = new(apkStream, ZipArchiveMode.Read);

        try
        {
            UnityVersion detected = ApkAnalyzer.DetectUnityVersionFromArchive(archive);
            if (detected == UnityVersion.MinVersion)
            {
                patcher.Logger.Log("Failed to get Unity version, cannot patch (missing globalgamemanagers and data.unity3d).");
                patcher.Args.UnityVersion = UnityVersion.MinVersion;
                return false;
            }

            patcher.Args.UnityVersion = detected;
            return true;
        }
        catch (Exception ex)
        {
            patcher.Logger.Log("Failed to get Unity version, cannot patch.\n" + ex.ToString());
            patcher.Args.UnityVersion = UnityVersion.MinVersion;
            return false;
        }
    }
}
