# Template Zabbix 6.0 — VMware Cluster HCM By VMware Cluster Exporter

File: `zabbix_template_vmware_cluster_hcm.yaml`. Zabbix đọc thẳng `/metrics` của `vmware-cluster-exporter` bằng
HTTP agent (không cần mật khẩu vCenter trong Zabbix), LLD tự tìm cluster.

## Import / cập nhật

Configuration → Templates → **Import** (Zabbix 6.4+: Data collection → Templates) → chọn file YAML → giữ
**Update existing** → Import. UUID cố định nên import lại bản mới chỉ cập nhật template đang dùng, không tạo bản trùng,
host giữ nguyên lịch sử.

## Gắn cho host

Mỗi instance exporter = 1 host: interface Agent = IP máy chạy exporter (nếu Zabbix proxy chạy cùng máy: `127.0.0.1`,
*Monitored by proxy* = proxy đó), gắn template, đặt macro:

| Macro trên host | Ví dụ | Ý nghĩa |
|---|---|---|
| `{$VMC.PORT}` | `9821` | port của instance exporter |
| `{$VMC.CLUSTER.MATCHES}` | `^POOL18-XD$` | chỉ lấy cluster khớp regex (mặc định `.*` = tất cả) |
| `{$VMC.CLUSTER.NOT_MATCHES}` | `^POOL17$` | bỏ cluster khớp regex |

Tên cluster trong regex là tên thật trên vCenter (không phải tên hiển thị trên dashboard). Ngưỡng cảnh báo là macro,
đổi riêng 1 cluster bằng macro context, vd `{$VMC.CPU.CRIT:"POOL17"}` = 60. Bảng trigger đầy đủ: README chính của repo,
mục **Zabbix 6.0**.

## Cảnh báo đáng chú ý

- `Cluster X: vSAN Resync 435.2 GB Remaining, Running Longer Than 30m` — tên ghi luôn dung lượng còn phải resync;
  cột *Operational data* cập nhật theo giá trị mới nhất.
- `Exporter: Cannot Collect Data From vCenter` — exporter không đăng nhập / không lấy được số liệu vCenter
  (sai mật khẩu: menu `sudo bash menu.sh` → mục 3).
