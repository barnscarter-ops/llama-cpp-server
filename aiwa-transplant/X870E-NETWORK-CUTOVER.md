# X870E network cutover — exact steps

Captured live from the Z690 on 2026-08-17 23:xx. These are **Windows adapter
settings**, not properties of the cable or the NIC silicon. Recreate them on
the X870E after the swap.

## What is actually where — read this first

| Role | Adapter today (Z690) | Silicon | MAC | Moves? |
|---|---|---|---|---|
| Switch / Internet | `HomeFiber` | **Intel I225-V** (Z690 onboard) | `58-11-22-30-68-48` | **No — stays with the Z690** |
| Direct AIWA link | `AIWA Direct` | **Realtek** 2.5GbE add-in card | `1C-86-0B-3A-48-FB` | **Yes — this is the card you pull** |

The prep doc's cutover table lists the switch-facing adapter as the Realtek.
It is not — it is the Intel I225-V, and it is soldered to the Z690. Only the
Realtek add-in card comes out.

## The trap: two identical Realteks on the new board

The X870E's **onboard** NIC is also a "Realtek Gaming 2.5GbE Family
Controller" — the same string Device Manager shows for the card you're moving.
After the swap you will have two adapters with the same description and no way
to tell them apart by name.

**Identify by MAC, never by name or by which one Windows numbered first.**

```powershell
Get-NetAdapter | Where-Object InterfaceDescription -like '*Realtek*2.5*' |
  Format-Table Name, InterfaceDescription, MacAddress, Status, LinkSpeed -AutoSize
```

- MAC `1C-86-0B-3A-48-FB` → **the moved card** → AIWA direct link
- The other one → **X870E onboard** → switch / Internet

Get this backwards and the machine claims `192.168.1.10` on a cable that goes
nowhere while the direct link hunts for a gateway. It looks like a dead switch
port, and it is not.

## Sequence

1. **Old PC fully off**, its switch-facing cable unplugged. Do not let two
   machines hold `192.168.1.10`.
2. Boot the X870E. It comes up on onboard LAN, DHCP, `192.168.1.220`.
3. Rename for sanity, using the MACs above:

```powershell
# onboard -> switch role
Rename-NetAdapter -Name "<onboard adapter's current name>" -NewName "HomeFiber"
# moved card -> direct role
Rename-NetAdapter -Name "<moved card's current name>" -NewName "AIWA Direct"
```

4. Apply the static configurations:

```powershell
# Switch / Internet — gateway AND DNS
New-NetIPAddress -InterfaceAlias "HomeFiber" -IPAddress 192.168.1.10 -PrefixLength 24 -DefaultGateway 192.168.1.254
Set-DnsClientServerAddress -InterfaceAlias "HomeFiber" -ServerAddresses 8.8.8.8,8.8.4.4

# Direct AIWA link — NO gateway, NO DNS. Both omissions are deliberate.
New-NetIPAddress -InterfaceAlias "AIWA Direct" -IPAddress 10.110.10.2 -PrefixLength 30
```

A second default gateway on the direct link will fight the real one for
outbound routing and produce intermittent, maddening Internet failures. Leave
it off.

5. Verify **in this order** — the order isolates where a failure lives:

```powershell
ping 192.168.1.254        # gateway reachable
Resolve-DnsName google.com # DNS working
ping 192.168.1.12         # AIWA over the LAN
ping 10.110.10.1          # AIWA over the direct cable
```

If the last one fails, stop at cabling and addressing on the **X870E side**.
AIWA's own config is unchanged and correct — LAN `192.168.1.12/24` via
`192.168.1.254`, direct `10.110.10.1/30` with no gateway. Do not alter AIWA to
make a ping succeed.

## After networking is up

- Tailscale: the Z690 node (`100.124.216.11`) is gone with the machine.
  `cmb-workbench` (`100.124.41.115`) is already up, so remote access survives.
- Then run `NIGHT2-PHASE4-6-FROM-X870E.md` to pull the AIWA backup down.
