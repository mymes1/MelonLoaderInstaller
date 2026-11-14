using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;

namespace MelonLoader.Installer.Core.Diagnostics;

public sealed record ElfInspectionResult(
    bool IsValidElf,
    bool Is64Bit,
    bool IsLittleEndian,
    ushort Machine,
    string ArchitectureName,
    ulong MaxLoadSegmentAlignment,
    long FileSizeBytes,
    string? Error = null)
{
    public bool IsArm64 => IsValidElf && Is64Bit && Machine == 0xB7;
    public string? Soname { get; init; }
    public IReadOnlyList<string> NeededLibraries { get; init; } = [];
    public IReadOnlyList<string> ExportedSymbols { get; init; } = [];
    public IReadOnlyList<string> ImportedSymbols { get; init; } = [];
}

public static class ElfHeaderInspector
{
    public const ushort EM_386 = 0x03;
    public const ushort EM_ARM = 0x28;
    public const ushort EM_X86_64 = 0x3E;
    public const ushort EM_AARCH64 = 0xB7;

    private const uint PT_LOAD = 1;
    private const uint PT_DYNAMIC = 2;

    private const uint SHT_STRTAB = 3;
    private const uint SHT_DYNAMIC = 6;
    private const uint SHT_DYNSYM = 11;

    private const long DT_NULL = 0;
    private const long DT_NEEDED = 1;
    private const long DT_STRTAB = 5;
    private const long DT_STRSZ = 10;
    private const long DT_SONAME = 14;

    public static ElfInspectionResult Inspect(byte[] data, bool parseDynamicAndSymbols = true)
    {
        if (data.Length < 64)
            return new ElfInspectionResult(false, false, false, 0, "Invalid", 0, data.Length, "File too small to be ELF");

        if (data[0] != 0x7F || data[1] != (byte)'E' || data[2] != (byte)'L' || data[3] != (byte)'F')
            return new ElfInspectionResult(false, false, false, 0, "NotELF", 0, data.Length, "Missing ELF magic bytes");

        bool is64 = data[4] == 2;
        bool isLittleEndian = data[5] == 1;
        if (!isLittleEndian)
            return new ElfInspectionResult(true, is64, false, 0, "BigEndian", 0, data.Length, "Unexpected big-endian ELF");

        ushort machine = BitConverter.ToUInt16(data, 18);
        string arch = machine switch
        {
            EM_AARCH64 => "arm64-v8a (AArch64)",
            EM_ARM => "armeabi-v7a (ARM32)",
            EM_X86_64 => "x86_64",
            EM_386 => "x86",
            _ => $"Unknown (0x{machine:X4})"
        };

        ulong maxAlign = 0;
        var loadSegments = new List<(ulong VAddr, ulong Offset, ulong FileSize)>();
        ulong dynamicOffset = 0;
        ulong dynamicFileSize = 0;

        if (is64)
        {
            ulong ePhoff = BitConverter.ToUInt64(data, 32);
            ushort ePhentsize = BitConverter.ToUInt16(data, 54);
            ushort ePhnum = BitConverter.ToUInt16(data, 56);

            if (ePhentsize >= 56 && ePhoff > 0 && ePhoff + (ulong)ePhnum * ePhentsize <= (ulong)data.Length)
            {
                for (int i = 0; i < ePhnum; i++)
                {
                    int off = (int)(ePhoff + (ulong)i * ePhentsize);
                    uint pType = BitConverter.ToUInt32(data, off);
                    ulong pOffset = BitConverter.ToUInt64(data, off + 8);
                    ulong pVaddr = BitConverter.ToUInt64(data, off + 16);
                    ulong pFilesz = BitConverter.ToUInt64(data, off + 32);
                    ulong pAlign = BitConverter.ToUInt64(data, off + 48);

                    if (pType == PT_LOAD)
                    {
                        if (pAlign > maxAlign)
                            maxAlign = pAlign;
                        loadSegments.Add((pVaddr, pOffset, pFilesz));
                    }
                    else if (pType == PT_DYNAMIC)
                    {
                        dynamicOffset = pOffset;
                        dynamicFileSize = pFilesz;
                    }
                }
            }
        }

        string? soname = null;
        var needed = new List<string>();
        var exported = new List<string>();
        var imported = new List<string>();

        if (is64 && parseDynamicAndSymbols)
        {
            ParseElf64DynamicAndSymbols(
                data,
                loadSegments,
                dynamicOffset,
                dynamicFileSize,
                out soname,
                needed,
                exported,
                imported);
        }

        return new ElfInspectionResult(true, is64, isLittleEndian, machine, arch, maxAlign, data.Length)
        {
            Soname = soname,
            NeededLibraries = needed,
            ExportedSymbols = exported,
            ImportedSymbols = imported
        };
    }

    private static void ParseElf64DynamicAndSymbols(
        byte[] data,
        List<(ulong VAddr, ulong Offset, ulong FileSize)> loadSegments,
        ulong ptDynOff,
        ulong ptDynSize,
        out string? soname,
        List<string> needed,
        List<string> exported,
        List<string> imported)
    {
        soname = null;

        ulong eShoff = BitConverter.ToUInt64(data, 40);
        ushort eShentsize = BitConverter.ToUInt16(data, 58);
        ushort eShnum = BitConverter.ToUInt16(data, 60);

        bool parsedDynamicFromSh = false;

        if (eShoff > 0 && eShentsize >= 64 && eShnum > 0 &&
            eShoff + (ulong)eShnum * eShentsize <= (ulong)data.Length)
        {
            static (uint Type, ulong Offset, ulong Size, uint Link, ulong EntSize) ReadSh(byte[] buf, ulong shoff, ushort entsz, int idx)
            {
                int o = (int)(shoff + (ulong)idx * entsz);
                return (
                    BitConverter.ToUInt32(buf, o + 4),
                    BitConverter.ToUInt64(buf, o + 24),
                    BitConverter.ToUInt64(buf, o + 32),
                    BitConverter.ToUInt32(buf, o + 40),
                    BitConverter.ToUInt64(buf, o + 56));
            }

            for (int i = 0; i < eShnum; i++)
            {
                var sh = ReadSh(data, eShoff, eShentsize, i);
                if (sh.Type == SHT_DYNAMIC && sh.Link < eShnum &&
                    sh.Offset + sh.Size <= (ulong)data.Length)
                {
                    var strSh = ReadSh(data, eShoff, eShentsize, (int)sh.Link);
                    if (strSh.Type == SHT_STRTAB && strSh.Offset + strSh.Size <= (ulong)data.Length)
                    {
                        ReadDynamicEntries(data, sh.Offset, sh.Size, strSh.Offset, strSh.Size, out soname, needed);
                        parsedDynamicFromSh = true;
                    }
                }
                else if (sh.Type == SHT_DYNSYM && sh.Link < eShnum && sh.EntSize >= 24 &&
                         sh.Offset + sh.Size <= (ulong)data.Length)
                {
                    var strSh = ReadSh(data, eShoff, eShentsize, (int)sh.Link);
                    if (strSh.Type == SHT_STRTAB && strSh.Offset + strSh.Size <= (ulong)data.Length)
                    {
                        ulong count = sh.Size / sh.EntSize;
                        for (ulong sIdx = 0; sIdx < count; sIdx++)
                        {
                            int sOff = (int)(sh.Offset + sIdx * sh.EntSize);
                            uint stName = BitConverter.ToUInt32(data, sOff);
                            byte stInfo = data[sOff + 4];
                            ushort stShndx = BitConverter.ToUInt16(data, sOff + 6);

                            int bind = stInfo >> 4;
                            if (bind != 1 && bind != 2) // STB_GLOBAL or STB_WEAK
                                continue;

                            string? symName = ReadNullTerminatedAscii(data, strSh.Offset, strSh.Size, stName);
                            if (string.IsNullOrEmpty(symName))
                                continue;

                            if (stShndx == 0)
                                imported.Add(symName);
                            else
                                exported.Add(symName);
                        }
                    }
                }
            }
        }

        if (!parsedDynamicFromSh && ptDynOff > 0 && ptDynSize >= 16 && ptDynOff + ptDynSize <= (ulong)data.Length)
        {
            ulong strTabVaddr = 0;
            ulong strTabSz = 0;
            for (ulong off = ptDynOff; off + 16 <= ptDynOff + ptDynSize; off += 16)
            {
                long tag = BitConverter.ToInt64(data, (int)off);
                ulong val = BitConverter.ToUInt64(data, (int)off + 8);
                if (tag == DT_NULL)
                    break;
                if (tag == DT_STRTAB)
                    strTabVaddr = val;
                else if (tag == DT_STRSZ)
                    strTabSz = val;
            }

            if (strTabVaddr > 0 && TryMapVaddrToOffset(loadSegments, strTabVaddr, out ulong strOff))
            {
                ulong maxStrSz = strTabSz > 0 ? Math.Min(strTabSz, (ulong)data.Length - strOff) : ((ulong)data.Length - strOff);
                ReadDynamicEntries(data, ptDynOff, ptDynSize, strOff, maxStrSz, out soname, needed);
            }
        }
    }

    private static bool TryMapVaddrToOffset(
        List<(ulong VAddr, ulong Offset, ulong FileSize)> segments,
        ulong vaddr,
        out ulong offset)
    {
        foreach (var seg in segments)
        {
            if (vaddr >= seg.VAddr && vaddr < seg.VAddr + seg.FileSize)
            {
                offset = seg.Offset + (vaddr - seg.VAddr);
                return true;
            }
        }
        offset = 0;
        return false;
    }

    private static void ReadDynamicEntries(
        byte[] data,
        ulong dynOff,
        ulong dynSize,
        ulong strOff,
        ulong strSize,
        out string? soname,
        List<string> needed)
    {
        soname = null;
        for (ulong off = dynOff; off + 16 <= dynOff + dynSize; off += 16)
        {
            long tag = BitConverter.ToInt64(data, (int)off);
            ulong val = BitConverter.ToUInt64(data, (int)off + 8);
            if (tag == DT_NULL)
                break;

            if (tag == DT_NEEDED)
            {
                string? lib = ReadNullTerminatedAscii(data, strOff, strSize, val);
                if (!string.IsNullOrEmpty(lib) && !needed.Contains(lib, StringComparer.Ordinal))
                    needed.Add(lib);
            }
            else if (tag == DT_SONAME)
            {
                soname = ReadNullTerminatedAscii(data, strOff, strSize, val);
            }
        }
    }

    private static string? ReadNullTerminatedAscii(byte[] data, ulong strOff, ulong strSize, ulong relOffset)
    {
        if (relOffset >= strSize)
            return null;
        ulong start = strOff + relOffset;
        ulong end = strOff + strSize;
        if (start >= (ulong)data.Length)
            return null;
        if (end > (ulong)data.Length)
            end = (ulong)data.Length;

        int i = (int)start;
        int limit = (int)end;
        while (i < limit && data[i] != 0)
            i++;

        return i > (int)start ? Encoding.ASCII.GetString(data, (int)start, i - (int)start) : null;
    }

    public static ElfInspectionResult InspectStream(Stream stream, bool parseDynamicAndSymbols = true)
    {
        using MemoryStream ms = new();
        stream.CopyTo(ms);
        return Inspect(ms.ToArray(), parseDynamicAndSymbols);
    }

    public static bool ContainsAscii(byte[] data, string needle)
    {
        byte[] pattern = Encoding.ASCII.GetBytes(needle);
        return data.AsSpan().IndexOf(pattern) >= 0;
    }

    public static bool ContainsUtf16(byte[] data, string needle)
    {
        byte[] pattern = Encoding.Unicode.GetBytes(needle);
        return data.AsSpan().IndexOf(pattern) >= 0;
    }
}
