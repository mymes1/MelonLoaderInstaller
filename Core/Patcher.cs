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

            Logger.Log($"Copying [ {Args.TargetApkPath} ] to [ {Info.OutputBaseApkPath} ]");
            File.Copy(Args.TargetApkPath, Info.OutputBaseApkPath, overwrite: true);

            if (Args.IsSplit)
            {
                Logger.Log($"Copying [ {Args.LibraryApkPath} ] to [ {Info.OutputLibApkPath} ]");
                File.Copy(Args.LibraryApkPath!, Info.OutputLibApkPath!, overwrite: true);
            }

            if (Args.ExtraSplitApkPaths != null)
            {
                for (int i = 0; i < Args.ExtraSplitApkPaths.Length; i++)
                {
                    string from = Args.ExtraSplitApkPaths[i];
                    string to = Info.OutputExtraApkPaths![i];

                    Logger.Log($"Copying [ {from} ] to [ {to} ]");
                    File.Copy(from, to, overwrite: true);
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
}
