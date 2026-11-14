using System;

namespace MelonLoader.Installer.Core;

public enum LemonPatchStage
{
    Full,
    NoHooks,
    MinimalBootstrap,
    Il2CppInitOnly
}

public static class LemonPatchStageExtensions
{
    public static string ToAssetToken(this LemonPatchStage stage) => stage switch
    {
        LemonPatchStage.NoHooks => "no_hooks",
        LemonPatchStage.MinimalBootstrap => "minimal_bootstrap",
        LemonPatchStage.Il2CppInitOnly => "il2cpp_init_only",
        _ => "full"
    };

    public static LemonPatchStage Parse(string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
            return LemonPatchStage.Full;

        return value.Trim().ToLowerInvariant().Replace('-', '_') switch
        {
            "no_hooks" or "nohooks" or "stage1" => LemonPatchStage.NoHooks,
            "minimal_bootstrap" or "minimal" or "stage2" => LemonPatchStage.MinimalBootstrap,
            "il2cpp_init_only" or "il2cppinitonly" or "stage3" => LemonPatchStage.Il2CppInitOnly,
            "full" or "stage4" => LemonPatchStage.Full,
            _ => throw new ArgumentException($"Unknown patch stage '{value}'. Valid values: full, no-hooks, minimal-bootstrap, il2cpp-init-only.")
        };
    }
}

/// <summary>
/// Public-facing class for user set information
/// </summary>
public class PatchArguments
{
    public string TargetApkPath { get; internal set; } = "";
    public string LibraryApkPath { get; internal set; } = "";
    public string[] ExtraSplitApkPaths { get; internal set; } = [];

    public string OutputApkDirectory { get; internal set; } = "";

    public string TempDirectory { get; internal set; } = "";

    public string MelonDataPath { get; internal set; } = "";
    public string UnityDependenciesPath { get; internal set; } = "";

    public AssetRipper.Primitives.UnityVersion? UnityVersion { get; internal set; }
    public string PackageName { get; internal set; } = "";

    public bool IsSplit { get; internal set; }

    /// <summary>
    /// Preserving the original APK's libunity.so is the default because replacing it with generic
    /// UnityDependencies can crash games like com.sdt.port inside UnityPlayer.nativeRender.
    /// </summary>
    public bool ReplaceLibUnity { get; internal set; }

    public LemonPatchStage PatchStage { get; internal set; } = LemonPatchStage.Full;

    public PatchArguments(
        string targetApkPath,
        string libraryApkPath,
        string[] extraSplitApkPaths,
        string outputApkDirectory,
        string tempDirectory,
        string melonDataPath,
        string unityDependenciesPath,
        AssetRipper.Primitives.UnityVersion? unityVersion,
        string packageName,
        bool isSplit)
        : this(
            targetApkPath,
            libraryApkPath,
            extraSplitApkPaths,
            outputApkDirectory,
            tempDirectory,
            melonDataPath,
            unityDependenciesPath,
            unityVersion,
            packageName,
            isSplit,
            replaceLibUnity: false,
            patchStage: LemonPatchStage.Full)
    {
    }

    public PatchArguments(
        string targetApkPath,
        string libraryApkPath,
        string[] extraSplitApkPaths,
        string outputApkDirectory,
        string tempDirectory,
        string melonDataPath,
        string unityDependenciesPath,
        AssetRipper.Primitives.UnityVersion? unityVersion,
        string packageName,
        bool isSplit,
        bool replaceLibUnity,
        LemonPatchStage patchStage = LemonPatchStage.Full)
    {
        TargetApkPath = targetApkPath;
        LibraryApkPath = libraryApkPath;
        ExtraSplitApkPaths = extraSplitApkPaths;
        OutputApkDirectory = outputApkDirectory;
        TempDirectory = tempDirectory;
        MelonDataPath = melonDataPath;
        UnityDependenciesPath = unityDependenciesPath;
        UnityVersion = unityVersion;
        PackageName = packageName;
        IsSplit = isSplit;
        ReplaceLibUnity = replaceLibUnity;
        PatchStage = patchStage;
    }
}
