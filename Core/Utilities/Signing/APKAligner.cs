using System;
using System.Collections.Generic;
using System.IO;

namespace MelonLoader.Installer.Core.Utilities.Signing
{
    public class APKAligner
    {
        private const int BufferSize = 1024 * 1024;
        private const uint Zip64EocdSignature = 0x06064b50;
        private const uint Zip64LocatorSignature = 0x07064b50;

        public static void AlignApk(string path)
        {
            AlignApk(path, stripStaleV1Signatures: true);
        }

        public static void AlignApk(string path, bool stripStaleV1Signatures)
        {
            string dir = Path.GetDirectoryName(Path.GetFullPath(path)) ?? Path.GetTempPath();
            string tempPath = Path.Combine(dir, $".align_{Guid.NewGuid():N}.tmp");

            try
            {
                using (FileStream inFs = new(path, FileMode.Open, FileAccess.Read, FileShare.Read))
                using (FileMemory inMem = new(inFs))
                using (FileStream outFs = new(tempPath, FileMode.Create, FileAccess.Write, FileShare.None))
                using (FileMemory outMem = new(outFs))
                {
                    long eocdPos = FindEndOfCentralDirectory(inMem);
                    inMem.Position = eocdPos;
                    EndOfCentralDirectory eocd = new(inMem);

                    long cdOffset = eocd.OffsetOfCD;
                    ulong entryCount = eocd.NumberOfCDsOnDisk;

                    // Check for ZIP64 EOCD Locator immediately before EOCD
                    if (eocdPos >= 20)
                    {
                        inMem.Position = eocdPos - 20;
                        if (inMem.ReadUInt() == Zip64LocatorSignature)
                        {
                            inMem.ReadUInt(); // disk with zip64 eocd
                            long zip64EocdOffset = inMem.ReadLong();
                            if (zip64EocdOffset >= 0 && zip64EocdOffset + 56 <= eocdPos - 20)
                            {
                                inMem.Position = zip64EocdOffset;
                                if (inMem.ReadUInt() == Zip64EocdSignature)
                                {
                                    inMem.ReadULong(); // size of zip64 eocd record
                                    inMem.ReadUShort(); // version made by
                                    inMem.ReadUShort(); // version needed
                                    inMem.ReadUInt(); // disk number
                                    inMem.ReadUInt(); // cd start disk
                                    entryCount = inMem.ReadULong(); // entries on disk
                                    inMem.ReadULong(); // total entries
                                    inMem.ReadULong(); // cd size
                                    cdOffset = (long)inMem.ReadULong();
                                }
                            }
                        }
                    }

                    byte[] copyBuffer = new byte[BufferSize];
                    List<CentralDirectoryFileHeader> keptCds = new((int)Math.Min(entryCount, 65536));

                    inMem.Position = cdOffset;
                    for (ulong i = 0; i < entryCount; i++)
                    {
                        CentralDirectoryFileHeader cd = new(inMem);
                        long nextCdPos = inMem.Position;

                        if (stripStaleV1Signatures && IsStaleV1SignatureEntry(cd.FileName))
                        {
                            inMem.Position = nextCdPos;
                            continue;
                        }

                        (ulong compressedSize, ulong uncompressedSize, long lfhOffset, bool isZip64Entry) = ResolveCdSizesAndOffset(cd);

                        inMem.Position = lfhOffset;
                        LocalFileHeader lfh = new(inMem);

                        if (lfh.CompressionMethod == 0)
                        {
                            long align = cd.FileName.EndsWith(".so", StringComparison.OrdinalIgnoreCase) ? 16384L : 4L;
                            int pad = ComputeAlignmentPadding(outMem.Position, lfh.FileNameBytes.Length, lfh.ExtraField.Length, align);
                            if (lfh.ExtraField.Length + pad > ushort.MaxValue && align > 4L)
                            {
                                align = 4L;
                                pad = ComputeAlignmentPadding(outMem.Position, lfh.FileNameBytes.Length, lfh.ExtraField.Length, align);
                            }

                            if (pad > 0 && lfh.ExtraField.Length + pad <= ushort.MaxValue)
                            {
                                lfh.ExtraField = AppendAlignmentExtraField(lfh.ExtraField, pad);
                            }
                        }

                        long newOffset = outMem.Position;
                        lfh.Write(outMem);

                        if (compressedSize > 0)
                        {
                            FileMemory.CopyExact(inFs, outFs, checked((long)compressedSize), copyBuffer);
                        }

                        if ((lfh.GeneralPurposeFlag & 0x08) != 0)
                        {
                            DataDescriptor dd = new(inMem, isZip64Entry);
                            dd.Write(outMem);
                        }

                        if (newOffset >= uint.MaxValue)
                        {
                            cd.Offset = uint.MaxValue;
                            cd.ExtraField = UpsertZip64OffsetExtraField(cd.ExtraField, uncompressedSize, compressedSize, (ulong)newOffset);
                        }
                        else
                        {
                            cd.Offset = (uint)newOffset;
                        }

                        keptCds.Add(cd);
                        inMem.Position = nextCdPos;
                    }

                    long newCdOffset = outMem.Position;
                    foreach (CentralDirectoryFileHeader cd in keptCds)
                    {
                        cd.Write(outMem);
                    }
                    long newCdSize = outMem.Position - newCdOffset;

                    bool needsZip64 = keptCds.Count >= ushort.MaxValue ||
                                      newCdOffset >= uint.MaxValue ||
                                      newCdSize >= uint.MaxValue;

                    if (needsZip64)
                    {
                        long zip64EocdPos = outMem.Position;
                        WriteZip64EocdAndLocator(outMem, (ulong)keptCds.Count, (ulong)newCdSize, (ulong)newCdOffset, zip64EocdPos);
                        eocd.NumberOfCDs = ushort.MaxValue;
                        eocd.NumberOfCDsOnDisk = ushort.MaxValue;
                        eocd.SizeOfCD = newCdSize >= uint.MaxValue ? uint.MaxValue : (uint)newCdSize;
                        eocd.OffsetOfCD = newCdOffset >= uint.MaxValue ? uint.MaxValue : (uint)newCdOffset;
                    }
                    else
                    {
                        eocd.NumberOfCDs = (ushort)keptCds.Count;
                        eocd.NumberOfCDsOnDisk = (ushort)keptCds.Count;
                        eocd.SizeOfCD = (uint)newCdSize;
                        eocd.OffsetOfCD = (uint)newCdOffset;
                    }

                    eocd.Write(outMem);
                    outFs.Flush();
                }

                File.Move(tempPath, path, overwrite: true);
            }
            finally
            {
                try
                {
                    if (File.Exists(tempPath))
                        File.Delete(tempPath);
                }
                catch { }
            }
        }

        internal static long FindEndOfCentralDirectory(FileMemory memory)
        {
            long len = memory.Length();
            if (len < 22)
                throw new InvalidDataException($"APK file is too small ({len} bytes) to contain a valid ZIP End of Central Directory.");

            long minSearch = Math.Max(0, len - 65557);
            for (long pos = len - 22; pos >= minSearch; pos--)
            {
                memory.Position = pos;
                if (memory.ReadInt() != EndOfCentralDirectory.SIGNATURE)
                    continue;

                memory.Position = pos + 20;
                ushort commentLen = memory.ReadUShort();
                if (pos + 22 + commentLen == len)
                    return pos;
            }

            throw new InvalidDataException("Could not locate ZIP End of Central Directory record.");
        }

        private static bool IsStaleV1SignatureEntry(string fileName)
        {
            string normalized = fileName.Replace('\\', '/');
            if (!normalized.StartsWith("META-INF/", StringComparison.OrdinalIgnoreCase))
                return false;

            string sub = normalized["META-INF/".Length..];
            if (sub.Contains('/'))
                return false;

            return sub.Equals("MANIFEST.MF", StringComparison.OrdinalIgnoreCase) ||
                   sub.EndsWith(".SF", StringComparison.OrdinalIgnoreCase) ||
                   sub.EndsWith(".RSA", StringComparison.OrdinalIgnoreCase) ||
                   sub.EndsWith(".DSA", StringComparison.OrdinalIgnoreCase) ||
                   sub.EndsWith(".EC", StringComparison.OrdinalIgnoreCase) ||
                   sub.EndsWith(".SIG", StringComparison.OrdinalIgnoreCase);
        }

        private static int ComputeAlignmentPadding(long currentPos, int fileNameLen, int extraLen, long align)
        {
            long dataStart = currentPos + 30L + fileNameLen + extraLen;
            long rem = dataStart % align;
            return rem == 0 ? 0 : (int)(align - rem);
        }

        private static byte[] AppendAlignmentExtraField(byte[] existing, int pad)
        {
            byte[] result = new byte[existing.Length + pad];
            Buffer.BlockCopy(existing, 0, result, 0, existing.Length);

            if (pad >= 4)
            {
                // Valid Android ZIP alignment TLV (ID 0xD935)
                result[existing.Length] = 0x35;
                result[existing.Length + 1] = 0xD9;
                ushort payloadLen = (ushort)(pad - 4);
                result[existing.Length + 2] = (byte)(payloadLen & 0xFF);
                result[existing.Length + 3] = (byte)((payloadLen >> 8) & 0xFF);
            }

            return result;
        }

        private static (ulong compressedSize, ulong uncompressedSize, long offset, bool isZip64) ResolveCdSizesAndOffset(CentralDirectoryFileHeader cd)
        {
            ulong comp = cd.CompressedSize;
            ulong uncomp = cd.UncompressedSize;
            long offset = cd.Offset;
            bool isZip64 = false;

            if (cd.UncompressedSize != uint.MaxValue && cd.CompressedSize != uint.MaxValue && cd.Offset != uint.MaxValue)
                return (comp, uncomp, offset, false);

            byte[] extra = cd.ExtraField;
            int idx = 0;
            while (idx + 4 <= extra.Length)
            {
                ushort tag = BitConverter.ToUInt16(extra, idx);
                ushort size = BitConverter.ToUInt16(extra, idx + 2);
                idx += 4;
                if (idx + size > extra.Length)
                    break;

                if (tag == 0x0001)
                {
                    isZip64 = true;
                    int p = idx;
                    if (cd.UncompressedSize == uint.MaxValue && p + 8 <= idx + size)
                    {
                        uncomp = BitConverter.ToUInt64(extra, p);
                        p += 8;
                    }
                    if (cd.CompressedSize == uint.MaxValue && p + 8 <= idx + size)
                    {
                        comp = BitConverter.ToUInt64(extra, p);
                        p += 8;
                    }
                    if (cd.Offset == uint.MaxValue && p + 8 <= idx + size)
                    {
                        offset = checked((long)BitConverter.ToUInt64(extra, p));
                    }
                    break;
                }

                idx += size;
            }

            return (comp, uncomp, offset, isZip64);
        }

        private static byte[] UpsertZip64OffsetExtraField(byte[] existing, ulong uncompressedSize, ulong compressedSize, ulong newOffset)
        {
            using MemoryStream ms = new();
            int idx = 0;
            while (idx + 4 <= existing.Length)
            {
                ushort tag = BitConverter.ToUInt16(existing, idx);
                ushort size = BitConverter.ToUInt16(existing, idx + 2);
                if (idx + 4 + size > existing.Length)
                    break;
                if (tag != 0x0001)
                    ms.Write(existing, idx, 4 + size);
                idx += 4 + size;
            }

            // Write updated ZIP64 extra field with all three 64-bit values when offset >= 4GB
            Span<byte> z64 = stackalloc byte[4 + 24];
            BitConverter.TryWriteBytes(z64[0..2], (ushort)0x0001);
            BitConverter.TryWriteBytes(z64[2..4], (ushort)24);
            BitConverter.TryWriteBytes(z64[4..12], uncompressedSize);
            BitConverter.TryWriteBytes(z64[12..20], compressedSize);
            BitConverter.TryWriteBytes(z64[20..28], newOffset);
            ms.Write(z64);
            return ms.ToArray();
        }

        private static void WriteZip64EocdAndLocator(FileMemory outMem, ulong entryCount, ulong cdSize, ulong cdOffset, long zip64EocdPos)
        {
            // ZIP64 EOCD
            outMem.WriteUInt(Zip64EocdSignature);
            outMem.WriteULong(44); // size of remaining record
            outMem.WriteUShort(45);
            outMem.WriteUShort(45);
            outMem.WriteUInt(0);
            outMem.WriteUInt(0);
            outMem.WriteULong(entryCount);
            outMem.WriteULong(entryCount);
            outMem.WriteULong(cdSize);
            outMem.WriteULong(cdOffset);

            // ZIP64 EOCD Locator
            outMem.WriteUInt(Zip64LocatorSignature);
            outMem.WriteUInt(0);
            outMem.WriteULong((ulong)zip64EocdPos);
            outMem.WriteUInt(1);
        }
    }
}
