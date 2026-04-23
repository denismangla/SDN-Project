from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet
from ryu.lib.packet import ether_types
from ryu.lib.packet import ipv4
from ryu.ofproto import ofproto_v1_3


class LearningSwitch(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(LearningSwitch, self).__init__(*args, **kwargs)
        self.mac_to_port = {}

        # Counters
        self.flood_count = 0
        self.forward_count = 0

    def add_flow(self, datapath, priority, match, actions, buffer_id=None):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        inst = [parser.OFPInstructionActions(
            ofproto.OFPIT_APPLY_ACTIONS, actions)]

        if buffer_id:
            mod = parser.OFPFlowMod(
                datapath=datapath,
                buffer_id=buffer_id,
                priority=priority,
                match=match,
                instructions=inst
            )
        else:
            mod = parser.OFPFlowMod(
                datapath=datapath,
                priority=priority,
                match=match,
                instructions=inst
            )

        datapath.send_msg(mod)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        # Table-miss rule
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(
            ofproto.OFPP_CONTROLLER,
            ofproto.OFPCML_NO_BUFFER)]

        self.add_flow(datapath, 0, match, actions)

        self.logger.info("\n========== SWITCH CONNECTED ==========")
        self.logger.info("Switch ID: %s", datapath.id)
        self.logger.info("Table-miss flow installed")
        self.logger.info("======================================\n")

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):

        msg = ev.msg
        datapath = msg.datapath
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)

        if eth is None:
            return

        # Ignore unnecessary packets
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return
        if eth.ethertype == ether_types.ETH_TYPE_IPV6:
            return

        dst = eth.dst
        src = eth.src
        dpid = datapath.id

        # Initialize MAC table
        self.mac_to_port.setdefault(dpid, {})

        # Learn MAC
        self.mac_to_port[dpid][src] = in_port

        # Decide forwarding
        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
            action = "FORWARD"
        else:
            out_port = ofproto.OFPP_FLOOD
            action = "FLOOD"

        actions = [parser.OFPActionOutput(out_port)]

        if action == "FLOOD":
            self.flood_count += 1
            self.logger.info(
                "FLOOD  | src=%s -> dst=%s | in_port=%s | total_flood=%d",
                src, dst, in_port, self.flood_count
            )
        else:
            self.forward_count += 1
            self.logger.info(
                "FORWARD| src=%s -> dst=%s | out_port=%s | total_forward=%d",
                src, dst, out_port, self.forward_count
            )

        if out_port != ofproto.OFPP_FLOOD:

            match = parser.OFPMatch(
                in_port=in_port,
                eth_src=src,
                eth_dst=dst
            )

            if msg.buffer_id != ofproto.OFP_NO_BUFFER:
                self.add_flow(datapath, 100, match, actions, msg.buffer_id)
                return
            else:
                self.add_flow(datapath, 100, match, actions)

        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data

        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=data
        )

        datapath.send_msg(out)
