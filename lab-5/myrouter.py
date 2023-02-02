#!/usr/bin/env python3

'''
Basic IPv4 router (static routing) in Python.
'''
import os
import sys
import time
import switchyard
import ipaddress
from switchyard.lib.userlib import *

class ftItem():
    def __init__(self,p,m,nh,i):
        self.prefix=p
        self.mask=m
        self.nexthop=nh
        self.name=i
class queueItem():
    def __init__(self,pkt,match,icmp):
        self.pkt=pkt
        self.rounds=0
        self.time=0
        self.match=match
        self.icmp_info=icmp
def ping_reply(packet,dstip):
    ether=Ethernet()
    ether.ethertype=EtherType.IP
    ip=IPv4()
    ip.src=IPAddr(dstip)
    ip.dst=IPAddr(packet[IPv4].src)
    ip.protocol=IPProtocol.ICMP
    ip.ttl=64
    ip.ipid=0
    icmp=ICMP()
    icmp.icmptype=ICMPType.EchoReply
    icmp.icmpcode=ICMPCodeEchoReply.EchoReply
    
    #3icmp.icmpdata=packet[ICMP].icmpdata
    
    icmp.icmpdata.sequence=packet[ICMP].icmpdata.sequence
    icmp.icmpdata.identifier=packet[ICMP].icmpdata.identifier
    icmp.icmpdata.data=packet[ICMP].icmpdata.data
    return ether+ip+icmp


def construct_icmperror(ipsrc,ipdst,xtype,xcode=0,ttl=64,origpkt=None):
    #origpkt = Ethernet() + IPv4() + ICMP() 
    
    icmp = ICMP()
    icmp.icmptype = xtype
    icmp.icmpcode=xcode
    if not origpkt is None:
        i = origpkt.get_header_index(Ethernet)
        del origpkt[i]
        icmp.icmpdata.data = origpkt.to_bytes()[:28]
        icmp.icmpdata.origdgramlen = len(origpkt)
    ip = IPv4()
    ip.protocol = IPProtocol.ICMP
    ip.src=IPAddr(ipsrc)
    ip.dst=IPAddr(ipdst)
    ip.ttl=ttl
    ether=Ethernet()
    pkt = ether+ip + icmp
    return pkt


class Router(object):
    def __init__(self, net: switchyard.llnetbase.LLNetBase):
        self.net = net
        self.interfaces=net.interfaces()
        self.ip_list=[intf.ipaddr for intf in self.interfaces]
        self.mac_list=[intf.ethaddr for intf in self.interfaces]
        self.arp_table={}
        self.forward_table=[]
        self.q=[]
        for i in self.interfaces:
            prefix=IPv4Address(int(i.ipaddr)&int(i.netmask))
            tempNetmask=IPv4Address(i.netmask)
            temp=ftItem(prefix,tempNetmask,None,i.name)
            self.forward_table.append(temp)
        file=open("forwarding_table.txt")
        while True:
            l=file.readline()
            if not l:
                break
            else:
                l=l.strip('\n')
                d=l.split(" ")
                prefix=IPv4Address(d[0])
                netmask=IPv4Address(d[1])
                nh=IPv4Address(d[2])
                name=d[3]
                temp=ftItem(prefix,netmask,nh,name)
                self.forward_table.append(temp)

        for a in self.forward_table:
            print(a.prefix," ",a.mask," ",a.nexthop," ",a.name)

        # other initialization stuff here

    def handle_packet(self, recv: switchyard.llnetbase.ReceivedPacket):
        timestamp, ifaceName, packet = recv
        # TODO: your logic here
        log_info("Got a packet:{}".format(str(packet)))
        arp=packet.get_header(Arp)
        ipv4=packet.get_header(IPv4)
        if ipv4:
            head=packet[IPv4]
            for i in self.ip_list:
                if packet[IPv4].dst==i:
                    if packet.has_header(ICMP) and packet[ICMP].icmptype==ICMPType.EchoRequest:
                        packet=ping_reply(packet,i)
                        head=packet[IPv4]
                        print("pings")
                        break
                    else:
                        for i in self.interfaces:
                            if i.name==ifaceName:
                                p=i
                                break
                        packet=construct_icmperror(p.ipaddr,head.src,ICMPType.DestinationUnreachable,3,64,packet)
                        head=packet[IPv4]
                        print("not a ping")
            head.ttl-=1
            if head.ttl<=0:
                for i in self.interfaces:
                    if i.name==ifaceName:
                        p1=i
                        break
                packet=construct_icmperror(p1.ipaddr,head.src,ICMPType.TimeExceeded,0,64,packet)
                head=packet[IPv4]
                print("ttl exceed")
            #print("ipv4",head)
            pos=-1
            maxprifixlen=-1
            index=0
            for i in self.forward_table:
                if((int(head.dst)&int(i.mask))==int(i.prefix)):
                    netaddr=IPv4Network(str(i.prefix)+"/"+str(i.mask))
                    if netaddr.prefixlen>maxprifixlen:
                        maxprifixlen=netaddr.prefixlen
                        pos=index
                index+=1
            
            if pos ==-1:
                print("cannot match?")
                for i in self.interfaces:
                    if i.name==ifaceName:
                        p2=i
                        break
                packet=construct_icmperror(p2.ipaddr,head.src,ICMPType.DestinationUnreachable,0,64,packet)
                head=packet[IPv4]
                print("cannot match")
                pos=-1
                maxprifixlen=-1
                index=0
                for i in self.forward_table:
                    if((int(head.dst)&int(i.mask))==int(i.prefix)):
                        netaddr=IPv4Network(str(i.prefix)+"/"+str(i.mask))
                        if netaddr.prefixlen>maxprifixlen:
                            maxprifixlen=netaddr.prefixlen
                            pos=index
                    index+=1
                self.q.append(queueItem(packet,self.forward_table[pos],ifaceName))
            else:
                print("paclet enque")
                self.q.append(queueItem(packet,self.forward_table[pos],ifaceName))

        if arp is None:
            log_info("Not an arp packet")
        else:
            log_info("operation kind {}".format(str(arp.operation)))
            self.arp_table[arp.senderprotoaddr]=arp.senderhwaddr
            if arp.operation==1:
                log_info("arp requests")
                index =-1
                for i in range(len(self.ip_list)):
                    if self.ip_list[i]==arp.targetprotoaddr:
                        index =i
                        break
                if index!= -1:
                    log_info("match packet")
                    answer=create_ip_arp_reply(self.mac_list[index],arp.senderhwaddr,self.ip_list[index],arp.senderprotoaddr)
                    self.net.send_packet(ifaceName,answer)
                    log_info("send arp reply:{}".format(str(answer)))
            elif arp.operation==2:
                log_info("receive an arp reply")
                self.arp_table[arp.targetprotoaddr]=arp.targethwaddr
            else:
                log_info("receive unknown arp")
        log_info("Table shown as follows:")
        for k,v in self.arp_table.items():
            print(k,"\t",v)

    def start(self):
        '''A running daemon of the router.
        Receive packets until the end of time.
        '''
        
        while True:
            if len(self.q)!=0:
                for i in self.interfaces:
                    if i.name==self.q[0].match.name:
                        port=i
                if self.q[0].match.nexthop is None:
                    
                    targetip=self.q[0].pkt[IPv4].dst
                    print("None case")
                    print(targetip)
                else:
                    print("not none")
                    targetip=self.q[0].match.nexthop
                flag=0
                for (k,v) in self.arp_table.items():
                    if targetip==k:
                        self.q[0].pkt[Ethernet].dst=v
                        self.q[0].pkt[Ethernet].src=port.ethaddr
                        print("send pkt found in arptable",port)
                        self.net.send_packet(port,self.q[0].pkt)
                        flag=1
                        del(self.q[0])
                        break
                if flag==0:
                    if self.q[0].rounds>=5:
                        for i in self.interfaces:
                            if i.name == self.q[0].icmp_info:
                                p4=i
                                break
                        packet=construct_icmperror(p4.ipaddr,self.q[0].pkt[IPv4].src,ICMPType.DestinationUnreachable,1,64,self.q[0].pkt)
                        head=packet[IPv4]
                        print("arp fail")
                        pos=-1
                        maxprifixlen=-1
                        index=0
                        for i in self.forward_table:
                            if((int(head.dst)&int(i.mask))==int(i.prefix)):
                                netaddr=IPv4Network(str(i.prefix)+"/"+str(i.mask))
                                if netaddr.prefixlen>maxprifixlen:
                                    maxprifixlen=netaddr.prefixlen
                                    pos=index
                            index+=1
                        del(self.q[0])
                        newq=queueItem(packet,self.forward_table[pos],p4.name)
                       
                        self.q.append(newq)
                    else:
                        cur=time.time()
                        if(self.q[0].rounds==0) or (cur-self.q[0].time>1):
                            ether=Ethernet()
                            ether.src=port.ethaddr
                            ether.dst='ff:ff:ff:ff:ff:ff'
                            ether.ethertype=EtherType.ARP
                            arp=Arp(operation=ArpOperation.Request,senderhwaddr=port.ethaddr,senderprotoaddr=port.ipaddr,targethwaddr='ff:ff:ff:ff:ff:ff',targetprotoaddr=targetip)
                            arppkt=ether+arp
                            print("send arp request",port)
                            self.net.send_packet(port,arppkt)
                            self.q[0].rounds+=1
                            self.q[0].time=time.time()

            try:
                recv = self.net.recv_packet(timeout=1.0)
            except NoPackets:
                continue
            except Shutdown:
                break

            self.handle_packet(recv)

        self.stop()

    def stop(self):
        self.net.shutdown()


def main(net):
    '''
    Main entry point for router.  Just create Router
    object and get it going.
    '''
    router = Router(net)
    router.start()
