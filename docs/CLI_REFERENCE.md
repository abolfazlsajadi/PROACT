# CLI reference

Generated from the current parser. No command was dispatched and no device was accessed.

Global options (`--port`, `--no-color`) precede the subcommand.

```text
usage: proact [-h] [--port PORT] [--no-color] [--version]
              {info,doctor,devices,status,timer,version,build-controller,build-target,test,run,aead-kat,decrypt-soft,seed,cpa,capture,program,reset,restart,peek,poke,selfcheck,selftest,load-swrv,monitor,gui}
              ...

proact -- unified command-line interface (installed as the `proact` entry
point via pyproject.toml; on the bench use ./run_cli.sh). Same backend as
the GUI, so everything the GUI does is scriptable here.

INFO         info | doctor [--json] | devices | status [--watch] | timer | version
BUILD/LOAD   build-controller | build-target | program | load-swrv
RUN          run | capture | cpa | aead-kat | decrypt-soft | seed
CONTROL      reset | restart | peek | poke
CHECK        test | selfcheck | selftest
MISC         monitor | gui

Examples:
  ./run_cli.sh info
  ./run_cli.sh run --core aes1 --compare --timer
  ./run_cli.sh run --core ascon --key 00112233445566778899aabbccddeeff
  ./run_cli.sh decrypt-soft --cipher ascon --selftest
  ./run_cli.sh selfcheck --capture --platform fpga
  ./run_cli.sh peek --addr 0x20000000 --count 4
  ./run_cli.sh reset --mode run
Colors: automatic on a terminal; disable with --no-color or NO_COLOR=1.
Errors: a bench failure prints a short hint; PROACT_DEBUG=1 keeps the traceback.

positional arguments:
  {info,doctor,devices,status,timer,version,build-controller,build-target,test,run,aead-kat,decrypt-soft,seed,cpa,capture,program,reset,restart,peek,poke,selfcheck,selftest,load-swrv,monitor,gui}
    info                version, address map, key facts
    doctor              offline environment diagnostics; no device access
    devices             list bench USB devices
    status              read + decode the status register
    timer               read the trigger-window cycle counter
    version             print the version
    build-controller    make the controller firmware
    build-target        make the Sw-RV firmware
    test                offline host-side self-checks
    run                 run crypto operations and print results
    aead-kat            on-chip ASCON+Xoodyak reference-vector KAT
    decrypt-soft        software AEAD decrypt + tag verify (aead_soft)
    seed                seed the masking PRNG
    cpa                 run the CPA attack on a capture (offline, no board)
    capture             capture power traces
    program             load controller firmware over SPI
    reset               apply a reset preset and/or show line states
    restart             reboot the controller (restores run state)
    peek                raw bus read (CMD_PEEK)
    poke                raw bus write (CMD_POKE) -- know your address!
    selfcheck           the unified A-Z self-check (same as the GUI tab)
    selftest            on-chip debug self-test (prints firmware log)
    load-swrv           load + boot a program on the Sw-RV target
    monitor             dump raw UART output (noise-safe)
    gui                 launch the GUI

options:
  -h, --help            show this help message and exit
  --port PORT           serial port (default: auto-detect the MCP2200)
  --no-color            disable colored output
  --version             show program's version number and exit
```

## info

```text
usage: proact info [-h]

options:
  -h, --help  show this help message and exit
```

## doctor

```text
usage: proact doctor [-h] [--json]

options:
  -h, --help  show this help message and exit
  --json      machine-readable environment report
```

## devices

```text
usage: proact devices [-h]

options:
  -h, --help  show this help message and exit
```

## status

```text
usage: proact status [-h] [--watch SECS]

options:
  -h, --help    show this help message and exit
  --watch SECS  repeat every positive SECS
```

## timer

```text
usage: proact timer [-h]

options:
  -h, --help  show this help message and exit
```

## version

```text
usage: proact version [-h]

options:
  -h, --help  show this help message and exit
```

## build-controller

```text
usage: proact build-controller [-h] [--riscv RISCV]

options:
  -h, --help     show this help message and exit
  --riscv RISCV  toolchain prefix
```

## build-target

```text
usage: proact build-target [-h] [--riscv RISCV]

options:
  -h, --help     show this help message and exit
  --riscv RISCV
```

## test

```text
usage: proact test [-h]

options:
  -h, --help  show this help message and exit
```

## run

```text
usage: proact run [-h] --core {aes1,aes2,ascon,xoodyak,swrv} [--key KEY]
                  [--pt PT] [--nonce NONCE] [--ad AD] [--decrypt]
                  [--runs RUNS] [--random] [--compare] [--timer]
                  [--trig {auto,software,aes1,aes2,ascon,xoodyak,swrv}]
                  [--inttrig INTTRIG] [--json]

options:
  -h, --help            show this help message and exit
  --core {aes1,aes2,ascon,xoodyak,swrv}
  --key KEY             16-byte key (hex)
  --pt PT               16-byte input (hex)
  --nonce NONCE         AEAD nonce (default zeros)
  --ad AD               AEAD associated data (default zeros)
  --decrypt
  --runs RUNS           repeat N times (N > 0)
  --random              fresh random plaintext per run
  --compare             check AES/Sw-RV vs software reference
  --timer               read cycle count per run
  --trig {auto,software,aes1,aes2,ascon,xoodyak,swrv}
                        trigger-source mux (cfg_sel)
  --inttrig INTTRIG     AEAD in-core trigger phase (7-bit, default 0x12)
  --json                machine-readable output
```

## aead-kat

```text
usage: proact aead-kat [-h]

options:
  -h, --help  show this help message and exit
```

## decrypt-soft

```text
usage: proact decrypt-soft [-h] [--cipher {ascon,xoodyak}] [--key KEY]
                           [--nonce NONCE] [--ct CT] [--tag TAG] [--ad AD]
                           [--selftest]

options:
  -h, --help            show this help message and exit
  --cipher {ascon,xoodyak}
  --key KEY
  --nonce NONCE
  --ct CT               ciphertext hex (any length)
  --tag TAG
  --ad AD
  --selftest            validate both ciphers against the silicon's vectors
```

## seed

```text
usage: proact seed [-h] --value VALUE

options:
  -h, --help     show this help message and exit
  --value VALUE  32-bit seed (e.g. 0xACE1ACE1)
```

## cpa

```text
usage: proact cpa [-h] [--core {aes1,aes2,swrv}] [--capture CAPTURE]
                  [--filter FILTER] [--window WINDOW] [--plot PNG]

options:
  -h, --help            show this help message and exit
  --core {aes1,aes2,swrv}
                        which attack to run: aes1/aes2 use the last-round
                        ciphertext model, swrv the first-round S-box model
  --capture CAPTURE     capture .npz/.h5 (default: the matching file in
                        datasets/, so this works with no board)
  --filter FILTER       moving-average width: 'auto', an integer, or 1 to
                        disable (see the ChipWhisperer wiki page)
  --window WINDOW       sample window lo:hi, or 'auto' (default)
  --plot PNG            save the correlation figure
```

## capture

```text
usage: proact capture [-h] --core {aes1,aes2,ascon,xoodyak,swrv}
                      [--traces TRACES] [--output OUTPUT]
                      [--platform {asic,fpga}] [--key KEY] [--samples SAMPLES]
                      [--clock CLOCK] [--bitstream BITSTREAM] [--gain GAIN]
                      [--gain-mode {low,high}] [--no-auto-samples] [--fixed]
                      [--no-scope]

options:
  -h, --help            show this help message and exit
  --core {aes1,aes2,ascon,xoodyak,swrv}
  --traces TRACES       number of records to acquire; required budget depends
                        on the measured setup
  --output OUTPUT
  --platform {asic,fpga}
  --key KEY
  --samples SAMPLES     samples per trace; grown automatically to cover the
                        whole trigger window unless --no-auto-samples
  --clock CLOCK         target clock MHz
  --bitstream BITSTREAM
                        program this CW305 bitstream first (fpga)
  --gain GAIN           ADC gain in dB (default: per-core recommendation -- 10
                        dB for the hardware cores, 20 dB for swrv). Too low
                        wastes ADC range and costs traces; too high clips and
                        destroys leakage
  --gain-mode {low,high}
                        ADC gain mode (default low)
  --no-auto-samples     do not grow --samples to the measured trigger window
  --fixed               fixed input (default random)
  --no-scope            functional only, no traces
```

## program

```text
usage: proact program [-h] --vmem VMEM [--serial SERIAL]

options:
  -h, --help       show this help message and exit
  --vmem VMEM
  --serial SERIAL
```

## reset

```text
usage: proact reset [-h] [--mode {run,controller,global,spi,reset_all}]

options:
  -h, --help            show this help message and exit
  --mode {run,controller,global,spi,reset_all}
                        preset to apply (omit to just show states)
```

## restart

```text
usage: proact restart [-h] [--serial SERIAL]

options:
  -h, --help       show this help message and exit
  --serial SERIAL
```

## peek

```text
usage: proact peek [-h] --addr ADDR [--count COUNT]

options:
  -h, --help     show this help message and exit
  --addr ADDR
  --count COUNT  consecutive words
```

## poke

```text
usage: proact poke [-h] --addr ADDR --data DATA [DATA ...]

options:
  -h, --help            show this help message and exit
  --addr ADDR
  --data DATA [DATA ...]
                        unsigned 32-bit word(s)
```

## selfcheck

```text
usage: proact selfcheck [-h] [--capture] [--platform {asic,fpga}]
                        [--clock CLOCK] [--samples SAMPLES]
                        [--bitstream BITSTREAM] [--no-swrv] [--log LOG]

options:
  -h, --help            show this help message and exit
  --capture             include a real trace capture
  --platform {asic,fpga}
  --clock CLOCK         target clock MHz
  --samples SAMPLES
  --bitstream BITSTREAM
                        program this CW305 bitstream first (fpga)
  --no-swrv             skip the Sw-RV step
  --log LOG             also write a plain-text report
```

## selftest

```text
usage: proact selftest [-h]

options:
  -h, --help  show this help message and exit
```

## load-swrv

```text
usage: proact load-swrv [-h] --imem IMEM --dmem DMEM [--key KEY] [--pt PT]

options:
  -h, --help   show this help message and exit
  --imem IMEM
  --dmem DMEM
  --key KEY
  --pt PT
```

## monitor

```text
usage: proact monitor [-h] [--secs SECS]

options:
  -h, --help   show this help message and exit
  --secs SECS
```

## gui

```text
usage: proact gui [-h]

options:
  -h, --help  show this help message and exit
```
