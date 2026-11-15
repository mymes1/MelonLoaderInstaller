using System;
using System.IO;
using System.Text;
using System.Threading.Tasks;

namespace MelonLoader.Installer.Core.Utilities.Signing
{
    public class FileMemory : IDisposable, IAsyncDisposable
    {
        public Stream Stream { get; private set; }

        public long Position
        {
            get { return Stream.Position; }
            set { Stream.Position = value; }
        }

        public FileMemory(Stream stream)
        {
            this.Stream = stream;
        }

        public void Dispose()
        {
            Stream.Dispose();
        }

        public ValueTask DisposeAsync()
        {
            return Stream.DisposeAsync();
        }

        public long Length()
        {
            return Stream.Length;
        }

        public void WriteByte(byte value)
        {
            Stream.WriteByte(value);
        }

        public byte ReadBytes()
        {
            int b = Stream.ReadByte();
            if (b < 0)
                throw new EndOfStreamException("Unexpected end of stream while reading byte.");
            return (byte)b;
        }

        public void WriteBytes(byte[] bytes)
        {
            Stream.Write(bytes, 0, bytes.Length);
        }

        public byte[] ReadBytes(int count)
        {
            if (count < 0)
                throw new ArgumentOutOfRangeException(nameof(count), $"Cannot read negative byte count: {count}");
            if (count == 0)
                return [];
            byte[] bytes = new byte[count];
            Stream.ReadExactly(bytes, 0, count);
            return bytes;
        }

        public short ReadShort()
        {
            return BitConverter.ToInt16(ReadBytes(2), 0);
        }

        public ushort ReadUShort()
        {
            return BitConverter.ToUInt16(ReadBytes(2), 0);
        }

        public void WriteShort(short value)
        {
            WriteBytes(BitConverter.GetBytes(value));
        }

        public void WriteUShort(ushort value)
        {
            WriteBytes(BitConverter.GetBytes(value));
        }

        public int ReadInt()
        {
            return BitConverter.ToInt32(ReadBytes(4), 0);
        }

        public uint ReadUInt()
        {
            return BitConverter.ToUInt32(ReadBytes(4), 0);
        }

        public void WriteInt(int value)
        {
            WriteBytes(BitConverter.GetBytes(value));
        }

        public void WriteUInt(uint value)
        {
            WriteBytes(BitConverter.GetBytes(value));
        }

        public long ReadLong()
        {
            return BitConverter.ToInt64(ReadBytes(8), 0);
        }

        public void WriteLong(long value)
        {
            WriteBytes(BitConverter.GetBytes(value));
        }

        public ulong ReadULong()
        {
            return BitConverter.ToUInt64(ReadBytes(8), 0);
        }

        public void WriteULong(ulong value)
        {
            WriteBytes(BitConverter.GetBytes(value));
        }

        public string ReadString(int count)
        {
            return Encoding.UTF8.GetString(ReadBytes(count));
        }

        public void WriteString(string value)
        {
            WriteBytes(Encoding.UTF8.GetBytes(value));
        }

        public static int StringLength(string value)
        {
            return Encoding.UTF8.GetByteCount(value);
        }

        public static void CopyExact(Stream source, Stream destination, long byteCount, byte[] buffer)
        {
            long remaining = byteCount;
            while (remaining > 0)
            {
                int toRead = (int)Math.Min(remaining, buffer.Length);
                source.ReadExactly(buffer, 0, toRead);
                destination.Write(buffer, 0, toRead);
                remaining -= toRead;
            }
        }
    }
}
