using System;

namespace MelonLoader.Installer.Core.Utilities.Signing
{
    public class EndOfCentralDirectory
    {
        public static readonly int SIGNATURE = 0x06054b50;
        public ushort NumberOfDisk { get; set; }
        public ushort CDStartDisk { get; set; }
        public ushort NumberOfCDsOnDisk { get; set; }
        public ushort NumberOfCDs { get; set; }
        public uint SizeOfCD { get; set; }
        public uint OffsetOfCD { get; set; }
        public byte[] CommentBytes { get; set; }
        public string Comment
        {
            get => System.Text.Encoding.UTF8.GetString(CommentBytes);
            set => CommentBytes = System.Text.Encoding.UTF8.GetBytes(value ?? "");
        }

        public EndOfCentralDirectory(FileMemory memory)
        {
            int signature = memory.ReadInt();
            if (signature != SIGNATURE)
                throw new Exception("Invalid EndOfCentralDirectory signature " + signature.ToString("X4"));
            NumberOfDisk = memory.ReadUShort();
            CDStartDisk = memory.ReadUShort();
            NumberOfCDsOnDisk = memory.ReadUShort();
            NumberOfCDs = memory.ReadUShort();
            SizeOfCD = memory.ReadUInt();
            OffsetOfCD = memory.ReadUInt();
            ushort commentLength = memory.ReadUShort();
            CommentBytes = memory.ReadBytes(commentLength);
        }

        public void Write(FileMemory memory)
        {
            memory.WriteInt(SIGNATURE);
            memory.WriteUShort(NumberOfDisk);
            memory.WriteUShort(CDStartDisk);
            memory.WriteUShort(NumberOfCDsOnDisk);
            memory.WriteUShort(NumberOfCDs);
            memory.WriteUInt(SizeOfCD);
            memory.WriteUInt(OffsetOfCD);
            memory.WriteUShort((ushort)CommentBytes.Length);
            memory.WriteBytes(CommentBytes);
        }
    }
}
