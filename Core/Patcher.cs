using MelonLoader.Installer.Core.PatchSteps;
using System;
using System.IO;

namespace MelonLoader.Installer.Core;

/// <summary>
/// Main class that handles starting patches
/// </summary>
public class Patcher(PatchArguments arguments, IPatchLogger logger)
{
    public PatchArguments Args { get; internal set; } = arguments;
    public IPatchLogger Logger { get; internal set; } = logger;
    public PatchInfo Info { get; internal set; } = new(arguments);

    public bool Run()
    {
        bool success = true;

        try
        {
            Info.CreateDirectories();

            CopyIfDifferent(Args.TargetApkPath, Info.OutputBaseApkPath);

            if (!string.IsNullOrEmpty(Info.OutputLibApkPath))
            {
                CopyIfDifferent(Args.LibraryApkPath!, Info.OutputLibApkPath);
            }

            if (Args.ExtraSplitApkPaths != null && Info.OutputExtraApkPaths != null)
            {
                for (int i = 0; i < Args.ExtraSplitApkPaths.Length; i++)
                {
                    CopyIfDifferent(Args.ExtraSplitApkPaths[i], Info.OutputExtraApkPaths[i]);
                }
            }

            IPatchStep[] steps =
            [
                new DetectUnityVersion(),
                new DownloadUnityDeps(),
                new ExtractDependencies(),
                new ExtractUnityLibs(),
                new PatchManifest(),
                new InstallPlugins(),
                new RepackAPK(),
                new GenerateCertificate(),
                new AlignSign(),
                new CleanUp(),
            ];

            foreach (IPatchStep step in steps)
            {
                bool status = step.Run(this);
                if (!status)
                    throw new Exception($"Failed to complete patching.");
            }
        }
        catch (Exception ex)
        {
            Logger.Log("[ERROR] " + ex.ToString());
            success = false;
        }

        return success;
    }

    private void CopyIfDifferent(string sourcePath, string destPath)
    {
        string fullSrc = Path.GetFullPath(sourcePath);
        string fullDst = Path.GetFullPath(destPath);
        if (string.Equals(fullSrc, fullDst, StringComparison.OrdinalIgnoreCase))
            return;

        Logger.Log($"Copying [ {sourcePath} ] to [ {destPath} ]");
        File.Copy(sourcePath, destPath, overwrite: true);
    }
}
