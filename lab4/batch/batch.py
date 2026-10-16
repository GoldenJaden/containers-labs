import os


memory_mb = int(os.getenv("MEMORY_MB", "256"))
memory_load = bytearray(memory_mb * 1024 * 1024)

value = 0
while True:
    for index in range(len(memory_load)):
        value = (value * 33 + index) & 0xFF
        memory_load[index] = value
