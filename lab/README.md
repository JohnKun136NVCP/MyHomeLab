# Laboratorio de redes (CCNA)

Topologías de [Containerlab](https://containerlab.dev) con routers **FRR** (sintaxis muy
parecida a Cisco IOS). Corren en la PC Linux o en la Raspberry (las imágenes son arm64/amd64).

## Instalar Containerlab (una vez)
```bash
bash -c "$(curl -sL https://get.containerlab.dev)"
```

## 1. OSPF área 0 (`ospf/`)
```bash
sudo containerlab deploy -t lab/ospf/ospf.clab.yml
docker exec -it clab-ospf-r1 vtysh            # consola tipo IOS
docker exec -it clab-ospf-pc1 sh              # una PC
sudo containerlab destroy -t lab/ospf/ospf.clab.yml --cleanup
```

### Ejercicios
1. **Vecinos:** en R1 `show ip ospf neighbor` → deben aparecer 2.2.2.2 y 3.3.3.3 en `Full`.
2. **Tabla de rutas:** `show ip route ospf` → ¿por dónde llega R1 a 192.168.30.0/24?
3. **Conectividad:** desde PC1 `ping 192.168.30.10` y `traceroute 192.168.30.10`.
4. **Costo:** en R1
   ```
   configure terminal
   interface eth2
    ip ospf cost 100
   end
   ```
   Repite el traceroute: ahora va por R2. Explica por qué (costo acumulado).
5. **Falla de enlace:** `sudo containerlab tools netem set -n clab-ospf-r1 -i eth1 --loss 100`
   y mira cuánto tarda OSPF en converger (`show ip ospf neighbor`, timers hello/dead).
6. **Interfaces pasivas:** quita `passive-interface eth3` en R1 y observa con
   `docker exec clab-ospf-pc1 tcpdump -ni eth1` los hellos de OSPF llegando a la PC.
   ¿Por qué es mala idea dejarlas activas hacia los usuarios?
7. **ACL:** en R2 bloquea el ping de PC1 hacia PC2 solo si pasa por R2:
   `docker exec clab-ospf-r2 iptables -A FORWARD -s 192.168.10.0/24 -d 192.168.30.0/24 -p icmp -j DROP`
   (FRR no filtra paquetes; el filtrado del plano de datos se hace con iptables/nftables,
   igual que en un router Linux real).

## Siguiente: VLANs
Los Tenda no manejan VLANs. Con un switch administrable 802.1Q (TP-Link TL-SG105E/108E)
y la Pi como router-on-a-stick (`eth0.10`, `eth0.20`) puedes practicar VLANs reales y que
netwatch vea cada una como una red más (`network.networks` con `interface: eth0.10`).
